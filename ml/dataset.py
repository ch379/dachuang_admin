from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Iterable, Optional

import pandas as pd
from sqlalchemy import text

from app.db import engine


TRAFFIC_SQL = """
SELECT
    timestamp,
    site_name,
    approach,
    vehicle_count,
    avg_speed,
    weather,
    tmax,
    tmin
FROM traffic_data
WHERE (:site_name IS NULL OR site_name = :site_name)
  AND (:approach IS NULL OR approach = :approach)
ORDER BY timestamp ASC
"""


@dataclass(frozen=True)
class DatasetSplit:
    train_end: datetime
    valid_end: datetime
    test_end: datetime


DEFAULT_SPLIT = DatasetSplit(
    train_end=datetime(2025, 10, 10, 23, 55),
    valid_end=datetime(2025, 10, 16, 23, 55),
    test_end=datetime(2025, 10, 21, 23, 55),
)


def load_prediction_frame(
    site_name: Optional[str] = None,
    approach: Optional[str] = None,
) -> pd.DataFrame:
    return _load_prediction_frame_cached(site_name, approach).copy()


@lru_cache(maxsize=32)
def _load_prediction_frame_cached(
    site_name: Optional[str] = None,
    approach: Optional[str] = None,
) -> pd.DataFrame:
    with engine.begin() as conn:
        df = pd.read_sql_query(
            text(TRAFFIC_SQL),
            conn,
            params={"site_name": site_name, "approach": approach},
            parse_dates=["timestamp"],
        )
    if df.empty:
        return df

    grouped = (
        df.groupby(["timestamp", "site_name", "approach"], as_index=False)
        .agg(
            vehicle_count=("vehicle_count", "sum"),
            avg_speed=("avg_speed", "mean"),
            weather=("weather", _mode_or_first),
            tmax=("tmax", "mean"),
            tmin=("tmin", "mean"),
        )
        .sort_values(["site_name", "approach", "timestamp"])
        .reset_index(drop=True)
    )
    grouped["tmax"] = grouped["tmax"].round(2)
    grouped["tmin"] = grouped["tmin"].round(2)
    return grouped


def split_by_time(df: pd.DataFrame, split: DatasetSplit = DEFAULT_SPLIT) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = df[df["timestamp"] <= split.train_end].copy()
    valid = df[(df["timestamp"] > split.train_end) & (df["timestamp"] <= split.valid_end)].copy()
    test = df[(df["timestamp"] > split.valid_end) & (df["timestamp"] <= split.test_end)].copy()
    return train, valid, test


def iter_series_keys(df: pd.DataFrame) -> Iterable[tuple[str, str]]:
    pairs = df[["site_name", "approach"]].drop_duplicates().itertuples(index=False, name=None)
    return list(pairs)


def _mode_or_first(values: pd.Series) -> str:
    if values.empty:
        return "未知"
    mode = values.mode(dropna=True)
    if not mode.empty:
        return str(mode.iloc[0])
    return str(values.iloc[0])
