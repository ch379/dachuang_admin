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
    model_path = path.with_name("traffic_model.joblib")
    if not model_path.is_file():
        model_path = ROOT / metadata["model_path"]
    if not model_path.is_file():
        return None, None
    return joblib.load(model_path), metadata


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


def _load_series(version: str, site: str, direction: str, as_of: datetime | None, limit: int) -> pd.DataFrame:
    sql = """SELECT timestamp, site_name, direction, flow_5min AS vehicle_count,
                     avg_speed_site_kmh AS avg_speed, weather, tmax_c AS tmax, tmin_c AS tmin
              FROM traffic_approach_observation
              WHERE dataset_version=:version AND site_name=:site AND direction=:direction
                AND data_valid_flag=1"""
    params = {"version": version, "site": site, "direction": direction, "limit": limit}
    if as_of:
        sql += " AND timestamp<=:as_of"
        params["as_of"] = as_of.strftime("%Y-%m-%d %H:%M:%S")
    sql += " ORDER BY timestamp DESC LIMIT :limit"
    with SessionLocal() as db:
        frame = pd.read_sql_query(text(sql), db.connection(), params=params, parse_dates=["timestamp"])
    frame["approach"] = frame["direction"].map({"E2W": "东向西", "W2E": "西向东", "N2S": "北向南", "S2N": "南向北"})
    return frame.drop(columns=["direction"]).sort_values("timestamp").reset_index(drop=True)


@ttl_cache(seconds=300, maxsize=512)
def predict_versioned(version: str, site: str, direction: str, as_of_key: str | None) -> list[tuple[datetime, int]]:
    model, metadata = load_version_model(version)
    if model is None:
        return []
    as_of = datetime.strptime(as_of_key, "%Y-%m-%d %H:%M:%S") if as_of_key else None
    lookback = int(metadata["lookback_steps"])
    columns = metadata.get("features", {})
    numeric = columns.get("numeric", NUMERIC_FEATURES)
    categorical = columns.get("categorical", CATEGORICAL_FEATURES)
    lags = [int(name.rsplit("_", 1)[1]) for name in numeric if name.startswith(("lag_", "speed_lag_"))]
    minimum_history = max(lookback, *lags) + 1
    history = _load_series(version, site, direction, as_of, minimum_history)
    if len(history) < minimum_history:
        return []
    if not history["timestamp"].diff().iloc[1:].eq(timedelta(minutes=5)).all():
        return []
    if as_of is not None and history.iloc[-1].timestamp != as_of:
        return []

    current = history
    future_times, values = [], []
    for _ in range(12):
        latest = current.iloc[-1]
        features = build_tabular_features(current, lookback_steps=lookback, include_target=False)
        if features.empty or features.iloc[-1].timestamp != latest.timestamp:
            return []
        value = max(0.0, float(model.predict(features.iloc[[-1]][numeric + categorical])[0]))
        next_ts = latest.timestamp + timedelta(minutes=5)
        future_times.append(next_ts)
        values.append(value)
        candidate = latest.copy()
        candidate["timestamp"] = next_ts
        candidate["vehicle_count"] = value
        current = pd.concat([current.iloc[1:], candidate.to_frame().T.infer_objects()], ignore_index=True)
    return aggregate_predictions_to_10min(future_times, values, anchor_time=history.iloc[-1].timestamp)
