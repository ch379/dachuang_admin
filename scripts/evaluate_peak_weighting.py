"""Select peak-flow weights on validation data and evaluate the frozen choice."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from ml.baselines import _build_pipeline
from ml.versioned_inference import load_version_model
from scripts.evaluate_versioned_forecast import BUCKETS, GROUP, STEP, build_windows
from scripts.train_versioned_model import VERSION, load_frame


FOCUS = ("星光大道北延伸段", "北向南")
WEIGHTS = (1, 2, 4)
MAX_MAE_INCREASE = 0.02
MIN_SHORTFALL_REDUCTION = 0.10


def focus_rows(frame):
    return frame.site_name.eq(FOCUS[0]) & frame.approach.eq(FOCUS[1])


def train_bucket(bucket, train, validation, columns):
    thresholds = train.groupby(GROUP)[bucket].quantile(0.90)
    train_threshold = thresholds.reindex(pd.MultiIndex.from_frame(train[GROUP])).to_numpy()
    emphasized = focus_rows(train) & train[bucket].ge(train_threshold)
    models = {}
    for weight in WEIGHTS:
        model = _build_pipeline()
        model.fit(train[columns], train[bucket], regressor__sample_weight=np.where(emphasized, weight, 1))
        models[weight] = model
    predictions = {
        weight: np.rint(model.predict(validation[columns]).clip(0))
        for weight, model in models.items()
    }
    print(f"Trained {bucket}, validated weights {WEIGHTS}", flush=True)
    return bucket, (thresholds, models, predictions)


def metrics(frame, predictions, fitted):
    actual = frame[BUCKETS].to_numpy()
    error = predictions - actual
    threshold = np.column_stack([
        fitted[bucket][0].reindex(pd.MultiIndex.from_frame(frame[GROUP])).to_numpy()
        for bucket in BUCKETS
    ])
    peak = actual >= threshold
    focus = focus_rows(frame).to_numpy()
    focus_peak_error = error[focus][peak[focus]]
    peak_error = error[peak]
    return {
        "windows": len(frame),
        "mae": np.abs(error).mean(),
        "focus_mae": np.abs(error[focus]).mean(),
        "peak_n": len(peak_error),
        "peak_bias": peak_error.mean(),
        "peak_under_rate": (peak_error < 0).mean(),
        "focus_peak_n": len(focus_peak_error),
        "focus_peak_bias": focus_peak_error.mean(),
        "focus_peak_under_rate": (focus_peak_error < 0).mean(),
        "focus_peak_shortfall": np.maximum(-focus_peak_error, 0).mean(),
    }


def eligible(candidate, reference):
    return (
        candidate["mae"] <= reference["mae"] * (1 + MAX_MAE_INCREASE)
        and candidate["focus_peak_shortfall"] <= reference["focus_peak_shortfall"] * (1 - MIN_SHORTFALL_REDUCTION)
        and candidate["focus_peak_under_rate"] < reference["focus_peak_under_rate"]
    )


def print_scores(name, scores):
    print(name, flush=True)
    print(pd.DataFrame.from_dict(scores, orient="index").round(4).to_string(), flush=True)


def main():
    _, metadata = load_version_model(VERSION)
    split = {key: pd.Timestamp(value) for key, value in metadata["split"].items()}
    windows = build_windows(load_frame(), metadata["lookback_steps"])
    end = windows.timestamp + 12 * STEP
    columns = metadata["features"]["numeric"] + metadata["features"]["categorical"]
    train = windows[end <= split["train_end"]]
    validation = windows[
        (windows.timestamp >= split["train_end"])
        & (end <= split["valid_end"])
        & windows.timestamp.dt.minute.eq(0)
    ]
    with threadpool_limits(limits=1), ThreadPoolExecutor(max_workers=3) as executor:
        fitted = dict(executor.map(lambda bucket: train_bucket(bucket, train, validation, columns), BUCKETS))
        scores = {
            weight: metrics(validation, np.column_stack([fitted[bucket][2][weight] for bucket in BUCKETS]), fitted)
            for weight in WEIGHTS
        }
        print_scores("Validation (hourly origins)", scores)
        candidates = [weight for weight in WEIGHTS[1:] if eligible(scores[weight], scores[1])]
        selected = min(candidates, key=lambda weight: scores[weight]["focus_peak_shortfall"]) if candidates else 1
        print(f"Frozen weight: {selected}", flush=True)

        test = windows[
            (windows.timestamp >= split["valid_end"])
            & (end <= split["test_end"])
            & windows.timestamp.dt.minute.eq(0)
        ]
        previous = test.timestamp.dt.hour.isin([8, 12, 18])
        for name, subset in (
            ("Test: hourly origins outside previous comparison", test[~previous]),
            ("Test: 195 windows from previous comparison", test[previous].dropna(subset=[f"daily_{bucket}" for bucket in BUCKETS])),
        ):
            scores = {}
            for weight in dict.fromkeys((1, selected)):
                prediction = np.column_stack([
                    np.rint(fitted[bucket][1][weight].predict(subset[columns]).clip(0))
                    for bucket in BUCKETS
                ])
                scores[weight] = metrics(subset, prediction, fitted)
            print_scores(name, scores)
            if selected != 1:
                print(f"Meets the same thresholds: {eligible(scores[selected], scores[1])}", flush=True)


if __name__ == "__main__":
    main()
