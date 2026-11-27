from __future__ import annotations

import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import joblib
import pandas as pd
from sqlalchemy import text

from app.cache import ttl_cache
from app.db import SessionLocal
from ml.baselines import CATEGORICAL_FEATURES, NUMERIC_FEATURES
from ml.features import aggregate_predictions_to_10min, build_tabular_features


ROOT = Path(__file__).resolve().parents[1]
VERSION = "新版_20260811_排除S20"


@lru_cache(maxsize=2)
def load_version_model(version: str):
    path = ROOT / "artifacts" / "models" / version / "metadata.json"
    if not path.exists():
        return None, None
    metadata = json.loads(path.read_text(encoding="utf-8"))
    return joblib.load(metadata["model_path"]), metadata


def get_version_model_meta(version: str) -> dict:
    _, metadata = load_version_model(version)
    if not metadata:
        return {"model_name": "unavailable", "model_type": "unavailable", "status": "unavailable"}
    return {
        "model_name": "新版历史梯度提升模型",
        "model_type": metadata["model_type"],
        "valid_mae": metadata["valid"]["model_mae"],
        "test_mae": metadata["test"]["model_mae"],
        "valid_rmse": None,
        "test_rmse": metadata["test"]["rmse"],
        "valid_mape": None,
        "test_mape": metadata["test"]["mape"],
        "lookback_steps": metadata["lookback_steps"],
        "forecast_steps": 12,
        "status": "ready",
    }


def _load_series(version: str, site: str, direction: str, as_of: datetime | None) -> pd.DataFrame:
    sql = """SELECT timestamp, site_name, direction, flow_5min AS vehicle_count,
                     avg_speed_site_kmh AS avg_speed, weather, tmax_c AS tmax, tmin_c AS tmin
              FROM traffic_approach_observation
              WHERE dataset_version=:version AND site_name=:site AND direction=:direction
                AND data_valid_flag=1"""
    params = {"version": version, "site": site, "direction": direction}
    if as_of:
        sql += " AND timestamp<=:as_of"
        params["as_of"] = as_of.strftime("%Y-%m-%d %H:%M:%S")
    sql += " ORDER BY timestamp ASC"
    with SessionLocal() as db:
        frame = pd.read_sql_query(text(sql), db.connection(), params=params, parse_dates=["timestamp"])
    frame["approach"] = frame["direction"].map({"E2W":"东向西","W2E":"西向东","N2S":"北向南","S2N":"南向北"})
    return frame.drop(columns=["direction"])


@ttl_cache(seconds=300, maxsize=512)
def predict_versioned(version: str, site: str, direction: str, as_of_key: str | None) -> list[tuple[datetime, int]]:
    model, metadata = load_version_model(version)
    if model is None:
        return []
    as_of = datetime.strptime(as_of_key, "%Y-%m-%d %H:%M:%S") if as_of_key else None
    history = _load_series(version, site, direction, as_of).tail(400).reset_index(drop=True)
    if len(history) < 300:
        return []
    current = history.copy()
    future_times, values = [], []
    for step in range(12):
        latest = current.iloc[-1]
        next_ts = pd.Timestamp(latest.timestamp) + timedelta(minutes=5)
        candidate = {
            "timestamp": next_ts, "site_name": site, "approach": direction,
            "vehicle_count": float(latest.vehicle_count), "avg_speed": float(latest.avg_speed),
            "weather": latest.weather, "tmax": latest.tmax, "tmin": latest.tmin,
        }
        simulated = pd.concat([current, pd.DataFrame([candidate])], ignore_index=True)
        features = build_tabular_features(simulated, lookback_steps=metadata["lookback_steps"])
        if features.empty:
            return []
        row = features.iloc[[-1]][NUMERIC_FEATURES + CATEGORICAL_FEATURES]
        value = max(0.0, float(model.predict(row)[0]))
        future_times.append(next_ts.to_pydatetime())
        values.append(value)
        candidate["vehicle_count"] = value
        current = pd.concat([current, pd.DataFrame([candidate])], ignore_index=True).tail(400).reset_index(drop=True)
    return aggregate_predictions_to_10min(future_times, values, anchor_time=history.iloc[-1].timestamp)
