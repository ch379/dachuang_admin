"""Evaluate upper-quantile peak-flow alerts for the priority direction."""

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from ml.baselines import _build_pipeline
from ml.versioned_inference import load_version_model
from scripts.evaluate_peak_weighting import FOCUS, focus_rows
from scripts.evaluate_versioned_forecast import BUCKETS, STEP, build_windows
from scripts.train_versioned_model import VERSION, load_frame


QUANTILES = (0.8, 0.9)
MIN_PRECISION = 0.60


def predict(train, frame, columns, quantile):
    forecasts = []
    for bucket in BUCKETS:
        model = _build_pipeline()
        if quantile is not None:
            model.set_params(regressor__loss="quantile", regressor__quantile=quantile)
        model.fit(train[columns], train[bucket])
        forecasts.append(model.predict(frame[columns]).clip(0))
    return np.column_stack(forecasts)


def score(actual, prediction, threshold):
    peak = actual >= threshold
    alert = prediction >= threshold
    hits = int((alert & peak).sum())
    false_alarms = int((alert & ~peak).sum())
    misses = int((~alert & peak).sum())
    return {
        "hits": hits,
        "false_alarms": false_alarms,
        "misses": misses,
        "precision": hits / (hits + false_alarms) if hits + false_alarms else 0,
        "recall": hits / (hits + misses) if hits + misses else 0,
        "coverage": (actual <= prediction).mean(),
    }


def main():
    _, metadata = load_version_model(VERSION)
    split = {key: pd.Timestamp(value) for key, value in metadata["split"].items()}
    raw = load_frame()
    windows = build_windows(raw[focus_rows(raw)], metadata["lookback_steps"])
    columns = metadata["features"]["numeric"] + metadata["features"]["categorical"]
    end = windows.timestamp + 12 * STEP
    train = windows[end <= split["train_end"]]
    evaluation = windows[windows.timestamp.dt.minute.eq(0) & (end <= split["test_end"])]
    validation = evaluation[(evaluation.timestamp >= split["train_end"])
                            & (evaluation.timestamp + 12 * STEP <= split["valid_end"])]
    test = evaluation[evaluation.timestamp >= split["valid_end"]]
    threshold = train[BUCKETS].quantile(0.90).to_numpy()
    combined = pd.concat([validation, test])
    with threadpool_limits(limits=1):
        answers = {
            quantile: predict(train, combined, columns, quantile)
            for quantile in (None, *QUANTILES)
        }
    validation_scores = {
        key: score(validation[BUCKETS].to_numpy(), forecast[:len(validation)], threshold)
        for key, forecast in answers.items()
    }
    eligible = [q for q in QUANTILES if validation_scores[q]["precision"] >= MIN_PRECISION]
    selected = max(eligible, key=lambda q: (validation_scores[q]["recall"], validation_scores[q]["precision"])) if eligible else None
    print(FOCUS, "training P90 by interval", threshold)
    print(f"Validation windows: {len(validation)}")
    print(pd.DataFrame(list(validation_scores.values()), index=["mean", *QUANTILES]).round(3).to_string())
    print(f"Frozen upper quantile: {selected}")
    if selected is None:
        return
    prediction = {key: answers[key][len(validation):] for key in (None, selected)}
    previous = test.timestamp.dt.hour.isin([8, 12, 18]).to_numpy()
    for name, mask in (("other hours", ~previous), ("08:00, 12:00, 18:00", previous)):
        actual = test.loc[mask, BUCKETS].to_numpy()
        scores = [score(actual, values[mask], threshold) for values in prediction.values()]
        print(f"Test {name}, {int(mask.sum())} windows")
        print(pd.DataFrame(scores, index=["mean", selected]).round(3).to_string())


if __name__ == "__main__":
    main()
