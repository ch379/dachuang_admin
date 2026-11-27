import csv
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.service import list_versioned_prediction_options
from ml.dataset import DEFAULT_SPLIT


SOURCE = ("恒福路交叉口", "北向南")
DEFAULT_RECEIVER = ("星峰路—康田路交叉口", "东向西")
DIRECTION_CODES = {"东向西": "E2W", "西向东": "W2E", "北向南": "N2S", "南向北": "S2N"}
STREAM_FILE = (
    Path(__file__).resolve().parents[1]
    / "data2.0/data_delivery_20260811/processed/04_traffic_raw_long.csv"
)
STEP = timedelta(minutes=5)


@lru_cache(maxsize=1)
def _ramp_observations(path: Path, modified: int) -> dict[datetime, int]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return {
            datetime.fromisoformat(row["timestamp"]): int(float(row["vehicle_count"]))
            for row in csv.DictReader(file)
            if row["stream_id"] == "S03" and row["data_role"] == "downstream_reference"
            and row["vehicle_count"]
        }


def summarize_scenario(source, receiver, ramp, ratio, ramp_share, threshold):
    moved = source * ratio
    after = {
        "source": source - moved,
        "receiver": receiver + moved,
        "ramp": ramp - moved * ramp_share,
    }
    if (after["ramp"] < 0).any():
        raise ValueError("假设的匝道减量超过实测流量，请降低分流比例或匝道占比。")
    baseline = {"source": source, "receiver": receiver, "ramp": ramp}
    return {
        key: {"before": float(values.sum()), "after": float(after[key].sum())}
        for key, values in baseline.items()
    } | {
        "receiver_threshold": float(threshold),
        "receiver_peak": {"before": float(receiver.max()), "after": float(after["receiver"].max())},
        "receiver_high": {
            "before": int((receiver >= threshold).sum()),
            "after": int((after["receiver"] >= threshold).sum()),
        },
    }


def _direction_flow(db, version, selection, start, end):
    site, direction = selection
    rows = db.execute(text(
        "SELECT timestamp, flow_5min FROM traffic_approach_observation "
        "WHERE dataset_version=:version AND site_name=:site AND direction=:direction "
        "AND timestamp>=:start AND timestamp<=:end AND data_valid_flag=1 AND flow_5min IS NOT NULL"
    ), {
        "version": version, "site": site, "direction": DIRECTION_CODES[direction],
        "start": start.isoformat(sep=" "), "end": end.isoformat(sep=" "),
    })
    return pd.Series({datetime.fromisoformat(ts): count for ts, count in rows}, dtype=float)


def simulate_merge(db: Session, version: str, start: datetime, receiver: tuple[str, str],
                   diversion_percent: float, ramp_share_percent: float, lag_minutes: int):
    options = list_versioned_prediction_options(version)
    valid = {(option.site_name, direction) for option in options
             for direction in option.directions if option.site_name != SOURCE[0]}
    if receiver not in valid:
        raise ValueError("请选择其他路口中有新版观测数据的接收方向。")
    if start.tzinfo is not None or start.minute % 5 or start.second or start.microsecond:
        raise ValueError("请选择本地时间的 5 分钟整点。")
    times = pd.date_range(start, periods=12, freq=STEP)
    shifted = times + timedelta(minutes=lag_minutes)
    source = _direction_flow(db, version, SOURCE, times[0], times[-1]).reindex(times)
    receive = _direction_flow(db, version, receiver, shifted[0], shifted[-1]).reindex(shifted)
    ramp = pd.Series(_ramp_observations(STREAM_FILE, STREAM_FILE.stat().st_mtime_ns), dtype=float).reindex(shifted)
    if source.isna().any() or receive.isna().any() or ramp.isna().any():
        raise ValueError("所选时段缺少完整观测，请更换起始时间或接收方向。")

    history = _direction_flow(db, version, receiver, datetime(1970, 1, 1), DEFAULT_SPLIT.train_end)
    if history.empty:
        raise ValueError("接收方向缺少计算历史阈值所需的观测。")
    return summarize_scenario(source.to_numpy(), receive.to_numpy(), ramp.to_numpy(),
                              diversion_percent / 100, ramp_share_percent / 100, history.quantile(0.8))
