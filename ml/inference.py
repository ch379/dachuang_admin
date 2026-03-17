from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, Optional

import joblib
import numpy as np
import pandas as pd

from app.cache import ttl_cache
from app.config import get_settings
from ml.dataset import load_prediction_frame
from ml.features import add_time_features, aggregate_predictions_to_10min, build_tabular_features


@dataclass
class PredictionEngine:
    model_type: str
    model: Any
    metadata: Dict[str, Any]
    scaler: Any | None = None


MAX_FEATURE_HISTORY_STEPS = 320


def predict_next_hour(
    site_name: str,
    approach: str,
    as_of: Optional[datetime] = None,
) -> list[tuple[datetime, int]]:
    as_of_key = as_of.strftime("%Y-%m-%d %H:%M:%S") if as_of else None
    return _predict_next_hour_cached(site_name, approach, as_of_key)


@ttl_cache(seconds=300, maxsize=512)
def _predict_next_hour_cached(
    site_name: str,
    approach: str,
    as_of_key: Optional[str] = None,
) -> list[tuple[datetime, int]]:
    engine = load_prediction_engine()
    if not engine:
        return []
    as_of = datetime.strptime(as_of_key, "%Y-%m-%d %H:%M:%S") if as_of_key else None

    if engine.model_type in {"hist_gradient_boosting", "gradient_boosting_fallback"}:
        return _predict_hgb(engine, site_name, approach, as_of=as_of)
    if engine.model_type == "lstm":
        return _predict_lstm(engine, site_name, approach, as_of=as_of)
    if engine.model_type in {"naive", "seasonal_naive"}:
        return _predict_simple(engine, site_name, approach, as_of=as_of)
    return []


def get_history_window(
    site_name: str,
    approach: str,
    as_of: Optional[datetime] = None,
    required_steps: int = MAX_FEATURE_HISTORY_STEPS,
) -> pd.DataFrame:
    history = load_prediction_frame(site_name=site_name, approach=approach)
    if history.empty:
        return history
    history = history.sort_values("timestamp").reset_index(drop=True)
    if as_of:
        history = history[history["timestamp"] <= as_of].copy()
    if history.empty:
        return history
    return history.tail(required_steps).reset_index(drop=True)


@lru_cache(maxsize=1)
def load_prediction_engine() -> Optional[PredictionEngine]:
    settings = get_settings()
    meta_path = settings.best_model_metadata_path
    if not meta_path.exists():
        return None

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    model_type = metadata.get("model_type")
    model_path = metadata.get("model_path")
    if not model_type:
        return None

    scaler = None
    if model_type == "lstm":
        try:
            import tensorflow as tf
        except ImportError:
            return None
        model = tf.keras.models.load_model(model_path) if model_path else None
        scaler_path = metadata.get("scaler_path")
        scaler = joblib.load(scaler_path) if scaler_path else None
    else:
        model = joblib.load(model_path) if model_path else None
    return PredictionEngine(model_type=model_type, model=model, metadata=metadata, scaler=scaler)


def get_prediction_metadata() -> Dict[str, Any]:
    engine = load_prediction_engine()
    if not engine:
        return {
            "model_name": "unavailable",
            "model_type": "unavailable",
            "status": "unavailable",
        }

    metrics = engine.metadata.get("metrics", {})
    valid = metrics.get("valid", {})
    test = metrics.get("test", {})
    return {
        "model_name": engine.metadata.get("name", engine.model_type),
        "model_type": engine.model_type,
        "valid_mae": valid.get("mae"),
        "test_mae": test.get("mae"),
        "valid_rmse": valid.get("rmse"),
        "test_rmse": test.get("rmse"),
        "valid_mape": valid.get("mape"),
        "test_mape": test.get("mape"),
        "lookback_steps": engine.metadata.get("lookback_steps", metrics.get("lookback_steps")),
        "forecast_steps": engine.metadata.get("forecast_steps", metrics.get("forecast_steps")),
        "status": "ready",
    }


def _predict_hgb(
    engine: PredictionEngine,
    site_name: str,
    approach: str,
    as_of: Optional[datetime] = None,
) -> list[tuple[datetime, int]]:
    history = get_history_window(site_name=site_name, approach=approach, as_of=as_of)
    if len(history) < 30:
        return []
    history["vehicle_count"] = history["vehicle_count"].astype(float)
    history["avg_speed"] = history["avg_speed"].astype(float)

    future_times: list[datetime] = []
    preds: list[float] = []
    current = history.copy()
    latest = current.iloc[-1]
    model = engine.model
    for step in range(12):
        next_ts = pd.Timestamp(latest["timestamp"]) + timedelta(minutes=5 * (step + 1))
        candidate = {
            "timestamp": next_ts,
            "site_name": site_name,
            "approach": approach,
            "vehicle_count": current.iloc[-1]["vehicle_count"],
            "avg_speed": current.iloc[-1]["avg_speed"],
            "weather": current.iloc[-1]["weather"],
            "tmax": current.iloc[-1]["tmax"],
            "tmin": current.iloc[-1]["tmin"],
        }
        simulated = pd.concat([current, pd.DataFrame([candidate])], ignore_index=True)
        feature_frame = build_tabular_features(simulated, lookback_steps=engine.metadata.get("lookback_steps", 24))
        if feature_frame.empty:
            return []
        row = feature_frame.iloc[[-1]][engine.metadata["numeric_features"] + engine.metadata["categorical_features"]]
        pred = float(model.predict(row)[0])
        simulated.loc[simulated.index[-1], "vehicle_count"] = max(0.0, pred)
        current = simulated
        future_times.append(next_ts.to_pydatetime())
        preds.append(max(0.0, pred))

    return aggregate_predictions_to_10min(future_times, preds, anchor_time=latest["timestamp"])


def _predict_lstm(
    engine: PredictionEngine,
    site_name: str,
    approach: str,
    as_of: Optional[datetime] = None,
) -> list[tuple[datetime, int]]:
    history = get_history_window(site_name=site_name, approach=approach, as_of=as_of)
    if history.empty or engine.scaler is None or engine.model is None:
        return []

    metrics = engine.metadata.get("metrics", {})
    feature_columns = metrics.get("feature_columns") or engine.metadata.get("feature_columns")
    lookback_steps = int(engine.metadata.get("lookback_steps", metrics.get("lookback_steps", 24)))
    if len(history) < lookback_steps or not feature_columns:
        return []

    current = history.copy()
    current["vehicle_count"] = current["vehicle_count"].astype(float)
    current["avg_speed"] = current["avg_speed"].astype(float)
    featured = add_time_features(current.copy())
    window = featured[feature_columns].tail(lookback_steps).to_numpy(dtype=float)
    if len(window) < lookback_steps:
        return []

    scaled = engine.scaler.transform(window).reshape(1, lookback_steps, len(feature_columns))
    preds = np.asarray(engine.model.predict(scaled, verbose=0)).reshape(-1).tolist()
    preds = [max(0.0, float(value)) for value in preds]

    base_time = pd.Timestamp(current.iloc[-1]["timestamp"])
    future_times = [(base_time + timedelta(minutes=5 * (idx + 1))).to_pydatetime() for idx in range(len(preds))]
    return aggregate_predictions_to_10min(future_times, preds, anchor_time=current.iloc[-1]["timestamp"])


def _predict_simple(
    engine: PredictionEngine,
    site_name: str,
    approach: str,
    as_of: Optional[datetime] = None,
) -> list[tuple[datetime, int]]:
    history = get_history_window(site_name=site_name, approach=approach, as_of=as_of, required_steps=288)
    if history.empty:
        return []
    if history.empty:
        return []

    if engine.model_type == "seasonal_naive" and len(history) >= 24:
        base_series = history["vehicle_count"].tail(24).tolist()
    else:
        base_series = [int(history.iloc[-1]["vehicle_count"])] * 12

    start = pd.Timestamp(history.iloc[-1]["timestamp"]) + timedelta(minutes=5)
    times = [start + timedelta(minutes=5 * idx) for idx in range(12)]
    if len(base_series) < 12:
        base_series = (base_series * 12)[:12]
    return aggregate_predictions_to_10min(times, base_series[:12], anchor_time=history.iloc[-1]["timestamp"])
