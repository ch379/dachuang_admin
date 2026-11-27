from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import engine
from ml.baselines import CATEGORICAL_FEATURES, NUMERIC_FEATURES, _build_pipeline, evaluate_regression
from ml.dataset import DEFAULT_SPLIT
from ml.features import build_tabular_features


VERSION = "新版_20260811_排除S20"
OUT = ROOT / "artifacts" / "models" / VERSION


def load_frame() -> pd.DataFrame:
    with engine.begin() as conn:
        frame = pd.read_sql_query(
            text("""SELECT timestamp, site_name, direction, flow_5min AS vehicle_count,
                          avg_speed_site_kmh AS avg_speed, weather, tmax_c AS tmax, tmin_c AS tmin
                   FROM traffic_approach_observation
                   WHERE dataset_version=:version AND data_valid_flag=1
                   ORDER BY site_name, direction, timestamp"""),
            conn, params={"version": VERSION}, parse_dates=["timestamp"],
        )
    frame["approach"] = frame["direction"].map({"E2W":"东向西","W2E":"西向东","N2S":"北向南","S2N":"南向北"})
    return frame.drop(columns=["direction"])


def mae(y: pd.Series, pred: pd.Series) -> float:
    return float((y.reset_index(drop=True) - pred.reset_index(drop=True)).abs().mean())


def main() -> None:
    frame = load_frame()
    features = build_tabular_features(frame, lookback_steps=24)
    train = features[features.timestamp <= DEFAULT_SPLIT.train_end]
    valid = features[(features.timestamp > DEFAULT_SPLIT.train_end) & (features.timestamp <= DEFAULT_SPLIT.valid_end)]
    test = features[(features.timestamp > DEFAULT_SPLIT.valid_end) & (features.timestamp <= DEFAULT_SPLIT.test_end)]
    if min(len(train), len(valid), len(test)) == 0:
        raise SystemExit("Train/validation/test split has an empty partition")

    columns = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    model = _build_pipeline()
    model.fit(train[columns], train.target)
    valid_pred = model.predict(valid[columns]).clip(0)
    test_pred = model.predict(test[columns]).clip(0)
    # Seasonal-naive references: previous 5-minute sample and previous-day same time.
    metrics = {
        "version": VERSION,
        "train_rows": len(train), "valid_rows": len(valid), "test_rows": len(test),
        "valid": {
            "model_mae": mae(valid.target, pd.Series(valid_pred)),
            "naive_mae": mae(valid.target, valid.lag_1),
            "daily_naive_mae": mae(valid.target, valid.lag_288),
        },
        "test": {
            "model_mae": mae(test.target, pd.Series(test_pred)),
            "naive_mae": mae(test.target, test.lag_1),
            "daily_naive_mae": mae(test.target, test.lag_288),
            **evaluate_regression(model, test),
        },
        "split": {"train_end": str(DEFAULT_SPLIT.train_end), "valid_end": str(DEFAULT_SPLIT.valid_end), "test_end": str(DEFAULT_SPLIT.test_end)},
        "features": {"numeric": NUMERIC_FEATURES, "categorical": CATEGORICAL_FEATURES},
        "lookback_steps": 24,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    import joblib
    model_path = OUT / "traffic_model.joblib"
    joblib.dump(model, model_path)
    metadata = {**metrics, "model_type": "versioned_hist_gradient_boosting", "model_path": str(model_path.resolve())}
    (OUT / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
