"""Measure whether other intersections improve the priority direction forecast."""

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from ml.baselines import _build_pipeline
from ml.versioned_inference import load_version_model
from scripts.evaluate_peak_weighting import FOCUS, focus_rows
from scripts.evaluate_seasonal_residual import score
from scripts.evaluate_versioned_forecast import BUCKETS, STEP, build_windows
from scripts.train_versioned_model import VERSION, load_frame


def neighbor_features(raw, origins):
    other_sites = raw[raw.site_name.ne(FOCUS[0])]
    flows = other_sites.pivot(
        index="timestamp", columns=["site_name", "approach"], values="vehicle_count",
    )
    flows.columns = [f"{site}_{direction}" for site, direction in flows.columns]
    current = flows.reindex(origins)
    previous = flows.reindex(origins - pd.Timedelta(minutes=15)).set_axis(current.index)
    return pd.concat([
        current.add_suffix("_flow"),
        (current - previous).add_suffix("_change_15m"),
    ], axis=1).set_axis(origins.index)


def fit_predict(train, evaluation, numeric, categorical, extra):
    columns = numeric + extra + categorical
    forecasts = []
    for bucket in BUCKETS:
        model = _build_pipeline()
        transformer = model.named_steps["preprocessor"]
        numeric_pipe, category_pipe = (step[1] for step in transformer.transformers)
        transformer.set_params(transformers=[
            ("numeric", numeric_pipe, numeric + extra),
            ("categorical", category_pipe, categorical),
        ])
        model.fit(train[columns], train[bucket])
        forecasts.append(np.rint(model.predict(evaluation[columns]).clip(0)))
    return np.column_stack(forecasts)


def main():
    _, metadata = load_version_model(VERSION)
    split = {key: pd.Timestamp(value) for key, value in metadata["split"].items()}
    raw = load_frame()
    windows = build_windows(raw[focus_rows(raw)], metadata["lookback_steps"])
    neighbors = neighbor_features(raw, windows.timestamp)
    windows = windows.join(neighbors)
    end = windows.timestamp + 12 * STEP
    hourly = windows.timestamp.dt.minute.eq(0)
    train = windows[end <= split["train_end"]]
    validation = windows[hourly & (windows.timestamp >= split["train_end"]) & (end <= split["valid_end"])]
    test = windows[hourly & (windows.timestamp >= split["valid_end"]) & (end <= split["test_end"])]
    numeric = metadata["features"]["numeric"]
    categorical = metadata["features"]["categorical"]
    threshold = train[BUCKETS].quantile(0.90).to_numpy()
    evaluation = pd.concat([validation, test])
    with threadpool_limits(limits=1):
        predictions = {
            name: fit_predict(train, evaluation, numeric, categorical, extra)
            for name, extra in (("local_only", []), ("with_neighbors", list(neighbors.columns)))
        }
    validation_mask = np.arange(len(evaluation)) < len(validation)
    fixed_hours = evaluation.timestamp.dt.hour.isin([8, 12, 18]).to_numpy()
    print(f"{len(train)} training windows; {len(neighbors.columns)} additional features; threshold {threshold}")
    for name, mask in (
        ("Validation", validation_mask),
        ("Held-out dates", ~validation_mask),
        ("Held-out dates, 08:00/12:00/18:00", ~validation_mask & fixed_hours),
    ):
        actual = evaluation.loc[mask, BUCKETS].to_numpy()
        print(f"{name}: {mask.sum()} windows, {(actual >= threshold).any(axis=1).sum()} high-flow windows")
        scores = {
            method: score(actual, forecast[mask], threshold)
            for method, forecast in predictions.items()
        }
        print(pd.DataFrame.from_dict(scores, orient="index").round(3).to_string())


if __name__ == "__main__":
    main()
