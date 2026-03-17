from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd


WEATHER_MAP = {
    "晴": 0,
    "多云": 1,
    "阴": 2,
    "小雨": 3,
    "中雨": 4,
    "大雨": 5,
    "雷阵雨": 6,
    "雾": 7,
    "未知": 8,
}


@dataclass(frozen=True)
class FeatureConfig:
    lookback_steps: int = 24
    forecast_steps: int = 12


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    ts = frame["timestamp"]
    hour = ts.dt.hour + ts.dt.minute / 60.0
    weekday = ts.dt.weekday
    frame["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    frame["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    frame["weekday_sin"] = np.sin(2 * np.pi * weekday / 7)
    frame["weekday_cos"] = np.cos(2 * np.pi * weekday / 7)
    frame["is_workday"] = (weekday < 5).astype(int)
    frame["weather_code"] = frame["weather"].map(WEATHER_MAP).fillna(WEATHER_MAP["未知"]).astype(int)
    return frame


def build_tabular_features(df: pd.DataFrame, lookback_steps: int = 24) -> pd.DataFrame:
    frame = add_time_features(df)
    frame = frame.sort_values(["site_name", "approach", "timestamp"]).copy()
    group_cols = ["site_name", "approach"]

    for lag in range(1, lookback_steps + 1):
        frame[f"lag_{lag}"] = frame.groupby(group_cols)["vehicle_count"].shift(lag)

    frame["speed_lag_1"] = frame.groupby(group_cols)["avg_speed"].shift(1)
    frame["speed_lag_12"] = frame.groupby(group_cols)["avg_speed"].shift(12)
    frame["speed_lag_24"] = frame.groupby(group_cols)["avg_speed"].shift(24)
    frame["lag_36"] = frame.groupby(group_cols)["vehicle_count"].shift(36)
    frame["lag_48"] = frame.groupby(group_cols)["vehicle_count"].shift(48)
    frame["lag_144"] = frame.groupby(group_cols)["vehicle_count"].shift(144)
    frame["lag_288"] = frame.groupby(group_cols)["vehicle_count"].shift(288)
    frame["rolling_mean_3"] = frame.groupby(group_cols)["vehicle_count"].transform(
        lambda s: s.shift(1).rolling(3, min_periods=1).mean()
    )
    frame["rolling_mean_12"] = frame.groupby(group_cols)["vehicle_count"].transform(
        lambda s: s.shift(1).rolling(12, min_periods=1).mean()
    )
    frame["rolling_mean_24"] = frame.groupby(group_cols)["vehicle_count"].transform(
        lambda s: s.shift(1).rolling(24, min_periods=1).mean()
    )
    frame["rolling_std_12"] = frame.groupby(group_cols)["vehicle_count"].transform(
        lambda s: s.shift(1).rolling(12, min_periods=2).std()
    )
    frame["rolling_std_24"] = frame.groupby(group_cols)["vehicle_count"].transform(
        lambda s: s.shift(1).rolling(24, min_periods=2).std()
    )
    frame["delta_1_12"] = frame["lag_1"] - frame["lag_12"]
    frame["delta_1_24"] = frame["lag_1"] - frame["lag_24"]
    frame["delta_day"] = frame["lag_1"] - frame["lag_288"]
    frame["target"] = frame.groupby(group_cols)["vehicle_count"].shift(-1)
    return frame.dropna().reset_index(drop=True)


def build_lstm_sequences(
    df: pd.DataFrame,
    feature_columns: Sequence[str],
    config: FeatureConfig,
) -> tuple[np.ndarray, np.ndarray]:
    frame = add_time_features(df).sort_values(["site_name", "approach", "timestamp"]).copy()
    sequences: list[np.ndarray] = []
    targets: list[np.ndarray] = []

    for (_, _), group in frame.groupby(["site_name", "approach"]):
        values = group[list(feature_columns) + ["vehicle_count"]].to_numpy(dtype=float)
        max_idx = len(values) - config.forecast_steps + 1
        for idx in range(config.lookback_steps, max_idx):
            window = values[idx - config.lookback_steps : idx, :-1]
            target = values[idx : idx + config.forecast_steps, -1]
            sequences.append(window)
            targets.append(target)

    if not sequences:
        return (
            np.empty((0, config.lookback_steps, len(feature_columns))),
            np.empty((0, config.forecast_steps)),
        )

    return np.asarray(sequences, dtype=float), np.asarray(targets, dtype=float)


def aggregate_predictions_to_10min(
    timestamps: Sequence[pd.Timestamp],
    values: Sequence[float],
    anchor_time: pd.Timestamp | None = None,
) -> list[tuple[pd.Timestamp, int]]:
    if not timestamps or not values:
        return []
    result: list[tuple[pd.Timestamp, int]] = []
    ts_list = list(pd.to_datetime(list(timestamps)))
    value_list = list(values)
    base_anchor = pd.Timestamp(anchor_time) if anchor_time is not None else ts_list[0]
    for idx in range(0, min(len(ts_list), len(value_list)), 2):
        chunk_ts = ts_list[idx : idx + 2]
        chunk_values = value_list[idx : idx + 2]
        if not chunk_ts or not chunk_values:
            continue
        bucket_ts = base_anchor + pd.Timedelta(minutes=(idx // 2) * 10)
        avg_value = sum(chunk_values) / len(chunk_values)
        result.append((bucket_ts.to_pydatetime(), int(round(max(0, avg_value)))))
    return result
