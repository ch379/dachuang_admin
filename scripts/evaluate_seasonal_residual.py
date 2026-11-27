"""Compare a seasonal residual model with the current one-hour forecast."""

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from ml.baselines import _build_pipeline
from ml.versioned_inference import load_version_model, predict_versioned
from scripts.evaluate_peak_weighting import FOCUS, focus_rows
from scripts.evaluate_versioned_forecast import BUCKETS, DIRECTION_CODES, STEP, build_windows
from scripts.train_versioned_model import VERSION, load_frame


def make_features(raw, lookback):
    windows = build_windows(raw, lookback)
    observations = raw.set_index("timestamp")
    for minutes in (15, 30):
        index = windows.timestamp - pd.Timedelta(minutes=minutes)
        for column in ("vehicle_count", "avg_speed"):
            windows[f"{column}_change_{minutes}"] = windows[column] - observations[column].reindex(index).to_numpy()

    for index, bucket in enumerate(BUCKETS):
        times = [windows.timestamp + (2 * index + offset) * STEP for offset in (1, 2)]
        previous_week = [
            observations.vehicle_count.reindex(time - pd.Timedelta(days=7)).to_numpy()
            for time in times
        ]
        windows[f"weekly_{bucket}"] = (previous_week[0] + previous_week[1]) / 2
        windows[f"seasonal_{bucket}"] = windows[[f"daily_{bucket}", f"weekly_{bucket}"]].mean(axis=1)
    return windows.dropna(subset=[f"seasonal_{bucket}" for bucket in BUCKETS])


def fit_predict(train, evaluation, feature_names, trend_names):
    forecasts = []
    for bucket in BUCKETS:
        baseline = f"seasonal_{bucket}"
        extra = trend_names + [baseline, f"weekly_{bucket}", f"daily_{bucket}"]
        columns = feature_names + extra
        model = _build_pipeline()
        numeric, categorical = model.named_steps["preprocessor"].transformers[:2]
        model.set_params(preprocessor__transformers=[
            (numeric[0], numeric[1], numeric[2] + extra),
            categorical,
        ])
        model.fit(train[columns], train[bucket] - train[baseline])
        correction = model.predict(evaluation[columns])
        forecasts.append(np.rint((evaluation[baseline].to_numpy() + correction).clip(0)))
    return np.column_stack(forecasts)


def score(actual, predicted, thresholds):
    high = actual >= thresholds
    error = predicted - actual
    high_window = high.any(axis=1)
    # For tied maxima use the first peak interval for both series.
    peak_offset = (predicted[high_window].argmax(axis=1) - actual[high_window].argmax(axis=1)) * 10
    return {
        "MAE": np.abs(error).mean(),
        "peak_shortfall": np.maximum(-error[high], 0).mean(),
        "peak_under_rate": (error[high] < 0).mean(),
        "peak_size_MAE": np.abs(predicted[high_window].max(axis=1) - actual[high_window].max(axis=1)).mean(),
        "peak_time_MAE_min": np.abs(peak_offset).mean(),
        "peak_signed_offset_min": peak_offset.mean(),
    }


def report(name, windows, residual, thresholds):
    current = []
    available = []
    for index, row in enumerate(windows.itertuples()):
        forecast = predict_versioned(
            VERSION, row.site_name, DIRECTION_CODES[row.approach],
            row.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        )
        if len(forecast) == len(BUCKETS):
            current.append([value for _, value in forecast])
            available.append(index)
    windows = windows.iloc[available]
    actual = windows[BUCKETS].to_numpy()
    profiles = windows[[f"seasonal_{bucket}" for bucket in BUCKETS]].to_numpy()
    estimates = {"current": np.asarray(current), "seasonal": profiles, "residual": residual[available]}
    print(f"{name}: {len(windows)} matched windows, {(actual >= thresholds).any(axis=1).sum()} high-flow windows", flush=True)
    scores = {method: score(actual, forecast, thresholds) for method, forecast in estimates.items()}
    print(pd.DataFrame.from_dict(scores, orient="index").round(3).to_string(), flush=True)


def main():
    _, metadata = load_version_model(VERSION)
    split = {key: pd.Timestamp(value) for key, value in metadata["split"].items()}
    raw = load_frame()
    windows = make_features(raw[focus_rows(raw)], metadata["lookback_steps"])
    end = windows.timestamp + 12 * STEP
    train = windows[end <= split["train_end"]]
    origins = windows.timestamp.dt.minute.eq(0)
    validation = windows[origins & (windows.timestamp >= split["train_end"]) & (end <= split["valid_end"])]
    test = windows[origins & (windows.timestamp >= split["valid_end"]) & (end <= split["test_end"])]
    names = metadata["features"]["numeric"] + metadata["features"]["categorical"]
    trends = [
        "vehicle_count_change_15", "avg_speed_change_15", "vehicle_count_change_30", "avg_speed_change_30",
    ]
    thresholds = train[BUCKETS].quantile(0.90).to_numpy()
    print(f"{FOCUS}: {len(train)} training windows; thresholds {thresholds}", flush=True)
    with threadpool_limits(limits=1):
        prediction = fit_predict(train, pd.concat([validation, test]), names, trends)
        report("Validation", validation, prediction[:len(validation)], thresholds)
        report("Existing held-out dates (no new dates available)", test, prediction[len(validation):], thresholds)


if __name__ == "__main__":
    main()
