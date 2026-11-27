"""Compare one-hour traffic forecasts on the same held-out windows."""

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from ml.baselines import _build_pipeline
from ml.features import build_tabular_features
from ml.versioned_inference import load_version_model, predict_versioned
from scripts.train_versioned_model import VERSION, load_frame


GROUP = ["site_name", "approach"]
BUCKETS = [f"bucket_{i}" for i in range(1, 7)]
DIRECTION_CODES = {"东向西": "E2W", "西向东": "W2E", "北向南": "N2S", "南向北": "S2N"}
STEP = pd.Timedelta(minutes=5)


def build_windows(raw, lookback):
    raw = raw.sort_values(GROUP + ["timestamp"])
    flow = raw.set_index(GROUP + ["timestamp"]).vehicle_count
    frame = build_tabular_features(raw, lookback, include_target=False)
    for idx, bucket in enumerate(BUCKETS):
        first = frame.timestamp + STEP * (2 * idx + 1)
        second = first + STEP
        for name, offset in ((bucket, pd.Timedelta(0)), (f"daily_{bucket}", pd.Timedelta(days=1))):
            samples = [
                flow.reindex(pd.MultiIndex.from_arrays(
                    [frame.site_name, frame.approach, timestamp - offset]
                )).to_numpy()
                for timestamp in (first, second)
            ]
            frame[name] = (samples[0] + samples[1]) / 2
    return frame.dropna(subset=BUCKETS)


def evaluate():
    _, metadata = load_version_model(VERSION)
    split = {key: pd.Timestamp(value) for key, value in metadata["split"].items()}
    windows = build_windows(load_frame(), metadata["lookback_steps"])
    columns = metadata["features"]["numeric"] + metadata["features"]["categorical"]
    end = windows.timestamp + 12 * STEP
    train = windows[end <= split["train_end"]]
    test = windows[
        (windows.timestamp >= split["valid_end"])
        & (end <= split["test_end"])
        & windows.timestamp.dt.hour.isin([8, 12, 18])
        & windows.timestamp.dt.minute.eq(0)
    ].dropna(subset=[f"daily_{bucket}" for bucket in BUCKETS])

    with threadpool_limits(limits=1):
        direct = {}
        for bucket in BUCKETS:
            model = _build_pipeline()
            model.fit(train[columns], train[bucket])
            direct[bucket] = np.rint(model.predict(test[columns]).clip(0))
            print(f"Trained {bucket}", flush=True)

        records = []
        for idx, row in enumerate(test.itertuples()):
            forecast = predict_versioned(
                VERSION, row.site_name, DIRECTION_CODES[row.approach],
                row.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            )
            if len(forecast) != len(BUCKETS):
                continue
            for step, (bucket, (_, value)) in enumerate(zip(BUCKETS, forecast), 1):
                records.append({
                    "site_name": row.site_name, "approach": row.approach,
                    "origin": row.timestamp, "step": step, "actual": getattr(row, bucket),
                    "recursive": value, "direct": direct[bucket][idx],
                    "last": row.vehicle_count, "daily": getattr(row, f"daily_{bucket}"),
                })
    return pd.DataFrame(records), len(train), len(test)


def main():
    results, train_count, candidate_count = evaluate()
    methods = ["recursive", "direct", "last", "daily"]
    errors = results[methods].sub(results.actual, axis=0)
    peak = results.actual >= results.groupby(GROUP).actual.transform(lambda x: x.quantile(0.9))
    window_range = results.groupby(GROUP + ["origin"])[["actual", *methods]].agg(lambda x: x.max() - x.min())
    print(f"Training windows: {train_count}; matched test windows: {len(results) // 6}/{candidate_count}")
    print("MAE by 10-minute bucket")
    print(errors.abs().groupby(results.step).mean().round(3).to_string())
    print("Overall MAE")
    print(errors.abs().mean().round(3).to_string())
    print(f"Peak bucket count: {peak.sum()}")
    print("Peak mean signed error")
    print(errors[peak].mean().round(3).to_string())
    print("Peak underprediction share")
    print(errors[peak].lt(0).mean().round(3).to_string())
    print("Mean within-hour range")
    print(window_range.mean().round(3).to_string())
    print("MAE by direction")
    print(results[GROUP].join(errors.abs()).groupby(GROUP).mean().round(3).to_string())


if __name__ == "__main__":
    main()
