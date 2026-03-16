from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import joblib
import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import StandardScaler

from ml.dataset import DEFAULT_SPLIT, load_prediction_frame, split_by_time
from ml.features import FeatureConfig, add_time_features, build_lstm_sequences


LSTM_FEATURES = [
    "vehicle_count",
    "avg_speed",
    "tmax",
    "tmin",
    "hour_sin",
    "hour_cos",
    "weekday_sin",
    "weekday_cos",
    "is_workday",
    "weather_code",
]


def train_lstm_model(output_dir: Path) -> Dict[str, Any]:
    try:
        import tensorflow as tf
    except ImportError as exc:
        raise RuntimeError("TensorFlow is not installed. Install requirements-ml.txt to train LSTM.") from exc

    df = add_time_features(load_prediction_frame())
    train_df, valid_df, test_df = split_by_time(df, split=DEFAULT_SPLIT)
    config = FeatureConfig(lookback_steps=24, forecast_steps=12)

    x_train, y_train = build_lstm_sequences(train_df, LSTM_FEATURES, config)
    x_valid, y_valid = build_lstm_sequences(valid_df, LSTM_FEATURES, config)
    x_test, y_test = build_lstm_sequences(test_df, LSTM_FEATURES, config)
    if len(x_train) == 0 or len(x_valid) == 0 or len(x_test) == 0:
        raise ValueError("Not enough sequence data to train LSTM model.")

    scaler = StandardScaler()
    x_train_scaled = _scale_sequences(x_train, scaler, fit=True)
    x_valid_scaled = _scale_sequences(x_valid, scaler, fit=False)
    x_test_scaled = _scale_sequences(x_test, scaler, fit=False)

    model = tf.keras.Sequential(
        [
            tf.keras.layers.Input(shape=(config.lookback_steps, len(LSTM_FEATURES))),
            tf.keras.layers.LSTM(64),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(32, activation="relu"),
            tf.keras.layers.Dense(config.forecast_steps),
        ]
    )
    model.compile(optimizer="adam", loss="mse", metrics=["mae"])
    early_stop = tf.keras.callbacks.EarlyStopping(
        monitor="val_loss",
        patience=6,
        restore_best_weights=True,
    )
    history = model.fit(
        x_train_scaled,
        y_train,
        validation_data=(x_valid_scaled, y_valid),
        epochs=40,
        batch_size=64,
        verbose=0,
        callbacks=[early_stop],
    )

    valid_pred = model.predict(x_valid_scaled, verbose=0)
    test_pred = model.predict(x_test_scaled, verbose=0)
    metrics = {
        "valid": _metric_dict(y_valid, valid_pred),
        "test": _metric_dict(y_test, test_pred),
        "epochs_trained": len(history.history.get("loss", [])),
        "model_type": "lstm",
        "lookback_steps": config.lookback_steps,
        "forecast_steps": config.forecast_steps,
        "feature_columns": LSTM_FEATURES,
        "prediction_mode": "direct_multi_step",
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "lstm.keras"
    scaler_path = output_dir / "lstm_scaler.joblib"
    metadata_path = output_dir / "lstm_metadata.json"

    model.save(model_path)
    joblib.dump(scaler, scaler_path)
    metadata_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "model_path": str(model_path),
        "scaler_path": str(scaler_path),
        "metadata_path": str(metadata_path),
        "metrics": metrics,
    }


def _scale_sequences(values: np.ndarray, scaler: StandardScaler, fit: bool) -> np.ndarray:
    shape = values.shape
    flat = values.reshape(-1, shape[-1])
    flat_scaled = scaler.fit_transform(flat) if fit else scaler.transform(flat)
    return flat_scaled.reshape(shape)


def _metric_dict(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    y_true_flat = np.asarray(y_true, dtype=float).reshape(-1)
    y_pred_flat = np.asarray(y_pred, dtype=float).reshape(-1)
    mae = float(mean_absolute_error(y_true_flat, y_pred_flat))
    rmse = float(np.sqrt(mean_squared_error(y_true_flat, y_pred_flat)))
    non_zero = np.where(np.abs(y_true_flat) < 1e-6, 1.0, y_true_flat)
    mape = float(np.mean(np.abs((y_true_flat - y_pred_flat) / non_zero)) * 100.0)
    return {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mape": round(mape, 4),
    }
