from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import TrafficData
from app.schemas import (
    AnalysisPoint,
    DataTimeRange,
    DirectionRoute,
    IntersectionPoint,
    PredictionComparePoint,
    PredictionCompareSummary,
    PredictionOption,
    PredictionRecord,
    RoutePoint,
    TrafficRecord,
    WeatherData,
)
from ml.inference import predict_next_hour
from ml.inference import get_prediction_metadata as load_prediction_metadata


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
        "星峰左": "星峰路康田路交叉口",
        "星峰北": "星峰路交叉口",
        "星光大道": "星光大道北延申段",
    }
    return alias_map.get(road_name, road_name)


def _parse_point(raw: str) -> Optional[Tuple[float, float]]:
    if not raw or not isinstance(raw, str):
        return None
    try:
        lng, lat = raw.split(",")
        return float(lng), float(lat)
    except ValueError:
        return None


def _clean_path(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if not points:
        return []

    deduped: List[Tuple[float, float]] = []
    for point in points:
        if not deduped or abs(point[0] - deduped[-1][0]) > 1e-6 or abs(point[1] - deduped[-1][1]) > 1e-6:
            deduped.append(point)

    if len(deduped) <= 2:
        return deduped

    smoothed = [deduped[0]]
    for idx in range(1, len(deduped) - 1):
        prev_pt = deduped[idx - 1]
        curr_pt = deduped[idx]
        next_pt = deduped[idx + 1]
        smoothed.append(
            (
                round(prev_pt[0] * 0.2 + curr_pt[0] * 0.6 + next_pt[0] * 0.2, 6),
                round(prev_pt[1] * 0.2 + curr_pt[1] * 0.6 + next_pt[1] * 0.2, 6),
            )
        )
    smoothed.append(deduped[-1])
    return smoothed


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _projection_progress(
    anchor: Tuple[float, float],
    point: Tuple[float, float],
    target_point: Optional[Tuple[float, float]],
) -> float:
    if not target_point:
        return _distance(anchor, point)

    vx = target_point[0] - anchor[0]
    vy = target_point[1] - anchor[1]
    norm = (vx * vx + vy * vy) ** 0.5
    if norm <= 1e-9:
        return _distance(anchor, point)

    px = point[0] - anchor[0]
    py = point[1] - anchor[1]
    return (px * vx + py * vy) / norm


def _trim_reverse_points(
    anchor: Tuple[float, float],
    points: List[Tuple[float, float]],
    target_point: Optional[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    if len(points) <= 2:
        return points

    trimmed: List[Tuple[float, float]] = [points[0]]
    best_progress = 0.0
    epsilon = 1e-5

    for point in points[1:]:
        progress = _projection_progress(anchor, point, target_point)
        if progress < -epsilon:
            continue
        if progress + epsilon < best_progress:
            continue
        if _distance(trimmed[-1], point) <= 1e-6:
            continue
        trimmed.append(point)
        best_progress = max(best_progress, progress)

    return trimmed if len(trimmed) >= 2 else points[:2]


def _anchor_path(
    anchor: Tuple[float, float],
    points: List[Tuple[float, float]],
    target_point: Optional[Tuple[float, float]] = None,
) -> List[Tuple[float, float]]:
    if not points:
        return []

    nearest_idx = min(range(len(points)), key=lambda idx: _distance(anchor, points[idx]))
    left = list(reversed(points[: nearest_idx + 1]))
    right = points[nearest_idx:]

    if target_point is None:
        chosen = right if len(right) >= len(left) else left
    else:
        left_end = left[-1] if left else points[nearest_idx]
        right_end = right[-1] if right else points[nearest_idx]
        chosen = right if _distance(target_point, right_end) <= _distance(target_point, left_end) else left

    anchored: List[Tuple[float, float]] = [anchor]
    for point in chosen:
        if _distance(point, anchored[-1]) > 1e-6:
            anchored.append(point)

    return _trim_reverse_points(anchor, anchored, target_point)


@lru_cache(maxsize=1)
def load_route_anchor_map() -> Dict[str, Tuple[float, float]]:
    xlsx_path = Path("11.xlsx")
    if not xlsx_path.exists():
        return {}

    df = pd.read_excel(xlsx_path, sheet_name="Sheet1", header=None)
    anchors: Dict[str, Tuple[float, float]] = {}

    row_idx = 0
    while row_idx < len(df) - 1:
        row = df.iloc[row_idx]
        site_name_raw = row.iloc[0]
        if isinstance(site_name_raw, str) and site_name_raw.strip():
            site_name = _normalize_road_name(site_name_raw.strip()) or site_name_raw.strip()
            center_raw = df.iloc[row_idx + 1].iloc[0]
            center = _parse_point(center_raw) if isinstance(center_raw, str) else None
            if center:
                anchors[site_name] = center
            row_idx += 2
            continue
        row_idx += 1
    return anchors


@lru_cache(maxsize=1)
def load_route_cardinal_map() -> Dict[str, Dict[str, Tuple[float, float]]]:
    xlsx_path = Path("11.xlsx")
    if not xlsx_path.exists():
        return {}

    df = pd.read_excel(xlsx_path, sheet_name="Sheet1", header=None)
    cardinals: Dict[str, Dict[str, Tuple[float, float]]] = {}

    row_idx = 0
    while row_idx < len(df) - 1:
        row = df.iloc[row_idx]
        site_name_raw = row.iloc[0]
        if isinstance(site_name_raw, str) and site_name_raw.strip():
            site_name = _normalize_road_name(site_name_raw.strip()) or site_name_raw.strip()
            next_row = df.iloc[row_idx + 1]
            current: Dict[str, Tuple[float, float]] = {}
            for col_idx, name in enumerate(["锚点", "北", "南", "西", "东"]):
                raw = next_row.iloc[col_idx]
                point = _parse_point(raw) if isinstance(raw, str) else None
                if point:
                    current[name] = point
            if current:
                cardinals[site_name] = current
            row_idx += 2
            continue
        row_idx += 1
    return cardinals


@lru_cache(maxsize=1)
def load_direction_routes() -> List[DirectionRoute]:
    xlsx_path = Path("11.xlsx")
    if not xlsx_path.exists():
        return []

    df = pd.read_excel(xlsx_path, sheet_name="Sheet1", header=None)
    direction_names = {"北向南", "南向北", "东向西", "西向东"}
    routes: List[DirectionRoute] = []
    anchors = load_route_anchor_map()
    cardinal_map = load_route_cardinal_map()
    direction_target = {
        "北向南": "南",
        "南向北": "北",
        "东向西": "西",
        "西向东": "东",
    }

    row_idx = 0
    while row_idx < len(df) - 1:
        row = df.iloc[row_idx]
        site_name_raw = row.iloc[0]
        if isinstance(site_name_raw, str) and site_name_raw.strip():
            site_name = _normalize_road_name(site_name_raw.strip())
            next_row = df.iloc[row_idx + 1]
            anchor = anchors.get(site_name or site_name_raw.strip())
            cardinals = cardinal_map.get(site_name or site_name_raw.strip(), {})
            for col_idx, header in enumerate(row.tolist()):
                if header not in direction_names:
                    continue
                raw_path = next_row.iloc[col_idx]
                if not isinstance(raw_path, str) or not raw_path.strip():
                    continue
                points = [_parse_point(part.strip()) for part in raw_path.split(";")]
                clean_points = _clean_path([pt for pt in points if pt is not None])
                if anchor:
                    clean_points = _anchor_path(
                        anchor,
                        clean_points,
                        cardinals.get(direction_target.get(header, "")),
                    )
                if len(clean_points) < 2:
                    continue
                routes.append(
                    DirectionRoute(
                        site_name=site_name or site_name_raw.strip(),
                        direction=header,
                        path=[RoutePoint(longitude=lng, latitude=lat) for lng, lat in clean_points],
                    )
                )
            row_idx += 2
            continue
        row_idx += 1

    return routes


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
    anchors = load_route_anchor_map()
    rows = db.execute(
        select(TrafficData.site_name)
        .distinct()
        .order_by(TrafficData.site_name.asc())
    ).scalars()
    names = set(rows) | set(known.keys())

    data: List[IntersectionPoint] = []
    for name in sorted(names):
        lng, lat = anchors.get(name, known.get(name, (0.0, 0.0)))
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
    direction: Optional[str],
) -> List[PredictionRecord]:
    ts = normalize_ts(time_range)
    road_name = _normalize_road_name(road_name)
    if not road_name:
        raise ValueError("road_name is required for prediction.")
    if not direction:
        raise ValueError("direction is required for prediction.")

    predicted = predict_next_hour(site_name=road_name, approach=direction, as_of=ts)
    if not predicted:
        raise ValueError("Prediction model is not ready or no history is available for this road and direction.")

    return [
        PredictionRecord(
            timestamp=timestamp,
            site_name=road_name,
            approach=direction,
            predicted_vehicle_count=count,
        )
        for timestamp, count in predicted
    ]


def get_prediction_meta() -> dict:
    return load_prediction_metadata()


def list_prediction_options(db: Session) -> List[PredictionOption]:
    rows = db.execute(
        select(TrafficData.site_name, TrafficData.approach)
        .distinct()
        .order_by(TrafficData.site_name.asc(), TrafficData.approach.asc())
    ).all()
    grouped: Dict[str, List[str]] = {}
    for site_name, approach in rows:
        grouped.setdefault(site_name, []).append(approach)
    return [PredictionOption(site_name=site_name, directions=directions) for site_name, directions in grouped.items()]


def get_prediction_compare(
    db: Session,
    time_range: str,
    road_name: str,
    direction: str,
) -> PredictionCompareSummary:
    road_name = _normalize_road_name(road_name)
    ts = normalize_ts(time_range)
    if not ts:
        raise ValueError("time_range is required for prediction comparison.")
    if not road_name or not direction:
        raise ValueError("road_name and direction are required for prediction comparison.")

    predicted = predict_next_hour(site_name=road_name, approach=direction, as_of=ts)
    if not predicted:
        raise ValueError("Prediction model is not ready or no history is available for this road and direction.")

    future_end = predicted[-1][0]
    rows = get_traffic_data(
        db=db,
        time_range=None,
        start_time=ts.strftime("%Y-%m-%d %H:%M:%S"),
        end_time=future_end.strftime("%Y-%m-%d %H:%M:%S"),
        road_name=road_name,
        direction=direction,
        limit=5000,
    )
    actual_map = _aggregate_actual_prediction_window(rows)
    points: List[PredictionComparePoint] = []
    actual_values: List[float] = []
    predicted_values: List[float] = []

    for timestamp, predicted_count in predicted:
        actual_count = actual_map.get(timestamp, 0)
        points.append(
            PredictionComparePoint(
                timestamp=timestamp,
                predicted_vehicle_count=predicted_count,
                actual_vehicle_count=actual_count,
                abs_error=abs(predicted_count - actual_count),
            )
        )
        predicted_values.append(float(predicted_count))
        actual_values.append(float(actual_count))

    mae = sum(abs(p - a) for p, a in zip(predicted_values, actual_values)) / len(points)
    rmse = (sum((p - a) ** 2 for p, a in zip(predicted_values, actual_values)) / len(points)) ** 0.5
    mape_terms = [abs((p - a) / (a if abs(a) > 1e-6 else 1.0)) for p, a in zip(predicted_values, actual_values)]
    mape = sum(mape_terms) / len(mape_terms) * 100.0
    return PredictionCompareSummary(
        mae=round(mae, 4),
        rmse=round(rmse, 4),
        mape=round(mape, 4),
        points=points,
    )


def _aggregate_actual_prediction_window(rows: List[TrafficRecord]) -> Dict[datetime, int]:
    grouped: Dict[datetime, Dict[str, float]] = {}
    for row in rows:
        bucket_minute = (row.timestamp.minute // 10) * 10
        bucket = row.timestamp.replace(minute=bucket_minute, second=0, microsecond=0)
        current = grouped.get(bucket)
        if not current:
            current = {"sum": 0.0, "n": 0.0}
            grouped[bucket] = current
        current["sum"] += float(row.vehicle_count)
        current["n"] += 1.0
    return {
        bucket + timedelta(minutes=10): int(round(item["sum"] / item["n"])) if item["n"] else 0
        for bucket, item in grouped.items()
    }


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


def get_direction_routes() -> List[DirectionRoute]:
    return load_direction_routes()
