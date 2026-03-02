from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean
from typing import Dict, Iterable, List, Optional

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import TrafficData
from app.schemas import (
    AnalysisPoint,
    DataTimeRange,
    IntersectionPoint,
    PredictionRecord,
    TrafficRecord,
    WeatherData,
)


def normalize_ts(time_range: Optional[str]) -> Optional[datetime]:
    if not time_range:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(time_range, fmt)
        except ValueError:
            continue
    raise ValueError("time_range format should be YYYY-MM-DD or YYYY-MM-DD HH:MM[:SS]")


def _normalize_road_name(road_name: Optional[str]) -> Optional[str]:
    if not road_name:
        return None
    alias_map = {
        "星峰路左": "星峰路康田路交叉口",
        "星峰路北": "星峰路交叉口",
        "星光大道": "星光大道北延申段",
    }
    return alias_map.get(road_name, road_name)


def _apply_filters(
    stmt: Select[tuple[TrafficData]],
    time_range: Optional[str],
    start_time: Optional[str],
    end_time: Optional[str],
    road_name: Optional[str],
    direction: Optional[str],
) -> Select[tuple[TrafficData]]:
    filters = []
    ts = normalize_ts(time_range)
    start_ts = normalize_ts(start_time)
    end_ts = normalize_ts(end_time)
    if ts:
        filters.append(TrafficData.timestamp == ts)
    if start_ts:
        filters.append(TrafficData.timestamp >= start_ts)
    if end_ts:
        filters.append(TrafficData.timestamp <= end_ts)
    road_name = _normalize_road_name(road_name)
    if road_name:
        filters.append(TrafficData.site_name == road_name)
    if direction:
        filters.append(TrafficData.approach == direction)
    if filters:
        stmt = stmt.where(and_(*filters))
    return stmt


def list_intersections(db: Session) -> List[IntersectionPoint]:
    settings = get_settings()
    known = settings.key_intersections
    rows = db.execute(
        select(TrafficData.site_name)
        .distinct()
        .order_by(TrafficData.site_name.asc())
    ).scalars()
    names = set(rows) | set(known.keys())

    data: List[IntersectionPoint] = []
    for name in sorted(names):
        lng, lat = known.get(name, (0.0, 0.0))
        data.append(IntersectionPoint(site_name=name, longitude=lng, latitude=lat))
    return data


def get_traffic_data(
    db: Session,
    time_range: Optional[str],
    start_time: Optional[str],
    end_time: Optional[str],
    road_name: Optional[str],
    direction: Optional[str],
    limit: int = 500,
) -> List[TrafficRecord]:
    stmt = select(TrafficData)
    stmt = _apply_filters(stmt, time_range, start_time, end_time, road_name, direction)
    stmt = stmt.order_by(TrafficData.timestamp.desc(), TrafficData.site_name.asc()).limit(limit)

    rows: Iterable[TrafficData] = db.execute(stmt).scalars().all()
    return [
        TrafficRecord(
            timestamp=row.timestamp,
            site_name=row.site_name,
            approach=row.approach,
            movement=row.movement,
            vehicle_count=row.vehicle_count,
            avg_speed=row.avg_speed,
            weather=row.weather,
            tmax=row.tmax,
            tmin=row.tmin,
        )
        for row in rows
    ]


def get_weather(db: Session, time_range: Optional[str]) -> WeatherData:
    ts = normalize_ts(time_range)
    stmt = select(
        func.max(TrafficData.tmax),
        func.min(TrafficData.tmin),
        TrafficData.weather,
        func.count().label("cnt"),
    )
    if ts:
        stmt = stmt.where(TrafficData.timestamp == ts)
    stmt = stmt.group_by(TrafficData.weather).order_by(func.count().desc())
    row = db.execute(stmt).first()
    if not row:
        raise ValueError("No weather data found for the given conditions.")
    tmax, tmin, weather, _ = row
    return WeatherData(weather=weather, tmax=int(tmax), tmin=int(tmin))


def get_prediction_data(
    db: Session,
    time_range: Optional[str],
    road_name: Optional[str],
) -> List[PredictionRecord]:
    ts = normalize_ts(time_range) or datetime.now().replace(second=0, microsecond=0)
    target_sites = [road_name] if road_name else db.execute(
        select(TrafficData.site_name).distinct()
    ).scalars().all()

    records: List[PredictionRecord] = []
    for site in target_sites:
        history_stmt = (
            select(TrafficData.vehicle_count, TrafficData.approach)
            .where(TrafficData.site_name == site)
            .where(TrafficData.timestamp <= ts)
            .order_by(TrafficData.timestamp.desc())
            .limit(24)
        )
        history = db.execute(history_stmt).all()
        if not history:
            continue

        base = int(mean([item.vehicle_count for item in history]))
        major_approach = history[0].approach

        for hour_delta, factor in [(1, 1.05), (3, 1.1), (24, 0.95)]:
            pred_count = max(0, int(base * factor))
            records.append(
                PredictionRecord(
                    timestamp=ts + timedelta(hours=hour_delta),
                    site_name=site,
                    approach=major_approach,
                    predicted_vehicle_count=pred_count,
                )
            )

    return records


def get_analysis_data(
    db: Session,
    start_time: str,
    end_time: str,
    interval_minutes: int,
    road_name: Optional[str],
    direction: Optional[str],
) -> List[AnalysisPoint]:
    start_ts = normalize_ts(start_time)
    end_ts = normalize_ts(end_time)
    if not start_ts or not end_ts:
        raise ValueError("start_time and end_time are required.")
    if end_ts < start_ts + timedelta(hours=1):
        raise ValueError("end_time must be at least 1 hour later than start_time.")
    if interval_minutes not in (10, 20, 30, 60, 120):
        raise ValueError("interval_minutes must be one of 10, 20, 30, 60, 120.")

    stmt = select(TrafficData).where(
        and_(
            TrafficData.timestamp >= start_ts,
            TrafficData.timestamp <= end_ts,
        )
    )
    road_name = _normalize_road_name(road_name)
    if road_name:
        stmt = stmt.where(TrafficData.site_name == road_name)
    if direction:
        stmt = stmt.where(TrafficData.approach == direction)

    rows = db.execute(stmt.order_by(TrafficData.timestamp.asc())).scalars().all()
    if not rows:
        return []

    grouped: Dict[datetime, Dict[str, float]] = {}
    for row in rows:
        minute_group = (row.timestamp.minute // interval_minutes) * interval_minutes
        bucket = row.timestamp.replace(minute=minute_group, second=0, microsecond=0)
        current = grouped.get(bucket)
        if not current:
            current = {"vehicle_sum": 0.0, "speed_sum": 0.0, "n": 0.0}
            grouped[bucket] = current
        current["vehicle_sum"] += float(row.vehicle_count)
        current["speed_sum"] += float(row.avg_speed)
        current["n"] += 1.0

    result = []
    for bucket in sorted(grouped.keys()):
        item = grouped[bucket]
        n = item["n"] if item["n"] else 1.0
        result.append(
            AnalysisPoint(
                bucket_start=bucket,
                vehicle_count=round(item["vehicle_sum"] / n, 2),
                avg_speed=round(item["speed_sum"] / n, 2),
            )
        )
    return result


def get_data_time_range(db: Session) -> DataTimeRange:
    row = db.execute(select(func.min(TrafficData.timestamp), func.max(TrafficData.timestamp))).first()
    if not row or not row[0] or not row[1]:
        raise ValueError("No traffic data found.")
    return DataTimeRange(min_timestamp=row[0], max_timestamp=row[1])
