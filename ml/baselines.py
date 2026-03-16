from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Dict

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from ml.dataset import DEFAULT_SPLIT, load_prediction_frame, split_by_time
from ml.features import build_tabular_features


NUMERIC_FEATURES = [
    "avg_speed",
    "tmax",
    "tmin",
    "hour_sin",
    "hour_cos",
    "weekday_sin",
    "weekday_cos",
    "is_workday",
    "weather_code",
    "speed_lag_1",
    "speed_lag_12",
    "speed_lag_24",
    "rolling_mean_3",
    "rolling_mean_12",
    "rolling_mean_24",
    "rolling_std_12",
    "rolling_std_24",
    "delta_1_12",
    "delta_1_24",
    "delta_day",
    "lag_36",
    "lag_48",
    "lag_144",
    "lag_288",
]
NUMERIC_FEATURES.extend([f"lag_{idx}" for idx in range(1, 25)])
CATEGORICAL_FEATURES = ["site_name", "approach"]


@dataclass(frozen=True)
class BaselineArtifacts:
    model_path: Path
    metrics_path: Path
    metadata_path: Path


def train_hist_gradient_baseline(output_dir: Path) -> Dict[str, Any]:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")
    df = load_prediction_frame()
    feature_df = build_tabular_features(df, lookback_steps=24)
    train_df, valid_df, test_df = split_by_time(feature_df, split=DEFAULT_SPLIT)
    if train_df.empty or valid_df.empty or test_df.empty:
        raise ValueError("Not enough data to train baseline model.")

    pipeline = _build_pipeline()
    x_train = train_df[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
    y_train = train_df["target"]
    model_type = "hist_gradient_boosting"
    try:
        pipeline.fit(x_train, y_train)
    except PermissionError:
        pipeline = _build_pipeline(use_hist=False)
        pipeline.fit(x_train, y_train)
        model_type = "gradient_boosting_fallback"

    metrics = {
        "valid": evaluate_regression(pipeline, valid_df),
        "test": evaluate_regression(pipeline, test_df),
        "train_rows": int(len(train_df)),
        "valid_rows": int(len(valid_df)),
        "test_rows": int(len(test_df)),
        "model_type": model_type,
        "lookback_steps": 24,
        "forecast_steps": 12,
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "baseline_hgb.joblib"
    metrics_path = output_dir / "baseline_hgb_metrics.json"
    metadata_path = output_dir / "baseline_hgb_metadata.json"

    joblib.dump(pipeline, model_path)
    metadata = {
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "lookback_steps": 24,
        "forecast_steps": 12,
        "model_type": model_type,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "model_path": str(model_path),
        "metadata_path": str(metadata_path),
        "metrics_path": str(metrics_path),
        "metrics": metrics,
    }


def evaluate_naive_baselines() -> Dict[str, Any]:
    df = load_prediction_frame()
    feature_df = build_tabular_features(df, lookback_steps=24)
    _, valid_df, test_df = split_by_time(feature_df, split=DEFAULT_SPLIT)
    if valid_df.empty or test_df.empty:
        raise ValueError("Not enough data to evaluate naive baselines.")

    return {
        "naive": {
            "valid": _eval_naive(valid_df, lag_column="lag_1"),
            "test": _eval_naive(test_df, lag_column="lag_1"),
        },
        "seasonal_naive": {
            "valid": _eval_naive(valid_df, lag_column="lag_24"),
            "test": _eval_naive(test_df, lag_column="lag_24"),
        },
    }


def evaluate_regression(model: Pipeline, df: pd.DataFrame) -> Dict[str, float]:
    x = df[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
    y_true = df["target"].to_numpy(dtype=float)
    y_pred = model.predict(x)
    return _metric_dict(y_true, y_pred)


def _build_pipeline(use_hist: bool = True) -> Pipeline:
    numeric = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
        ]
    )
    categorical = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    preprocessor = ColumnTransformer(
        transformers=[
            ("numeric", numeric, NUMERIC_FEATURES),
            ("categorical", categorical, CATEGORICAL_FEATURES),
        ]
    )
    if use_hist:
        regressor = HistGradientBoostingRegressor(
            learning_rate=0.05,
            max_depth=7,
            max_iter=180,
            min_samples_leaf=16,
            random_state=42,
        )
    else:
        regressor = GradientBoostingRegressor(
            learning_rate=0.05,
            max_depth=4,
            n_estimators=180,
            random_state=42,
        )
    return Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("regressor", regressor),
        ]
    )


def _eval_naive(df: pd.DataFrame, lag_column: str) -> Dict[str, float]:
    subset = df.dropna(subset=[lag_column, "target"]).copy()
    y_true = subset["target"].to_numpy(dtype=float)
    y_pred = subset[lag_column].to_numpy(dtype=float)
    return _metric_dict(y_true, y_pred)


def _metric_dict(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    non_zero = np.where(np.abs(y_true) < 1e-6, 1.0, y_true)
    mape = float(np.mean(np.abs((y_true - y_pred) / non_zero)) * 100.0)
    return {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mape": round(mape, 4),
    }
