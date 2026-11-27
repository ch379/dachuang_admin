from __future__ import annotations

import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
from sqlalchemy import Select, and_, delete, func, select, text
from sqlalchemy.orm import Session

from app.cache import ttl_cache
from app.config import get_settings
from app.db import SessionLocal
from app.models import PredictionResult, TrafficData
from app.schemas import (
    AnalysisPoint,
    DataTimeRange,
    DirectionRoute,
    DirectionFlow,
    IntersectionPoint,
    PredictionComparePoint,
    PredictionCompareSummary,
    PredictionOption,
    PredictionRecord,
    RoutePoint,
    TrafficRecord,
    UserAlert,
    UserAlertCreate,
    WeatherData,
)
from ml.inference import predict_next_hour
from ml.inference import get_prediction_metadata as load_prediction_metadata


_user_alert_lock = Lock()
_user_alert_seq = 0
_user_alerts: List[UserAlert] = []
_USER_ALERT_TTL = timedelta(hours=3)
_USER_ALERT_MAX = 50


def normalize_ts(time_range: Optional[str]) -> Optional[datetime]:
    if not time_range:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(time_range, fmt)
        except ValueError:
            continue
    raise ValueError("time_range format should be YYYY-MM-DD or YYYY-MM-DD HH:MM[:SS]")


def align_to_ten_minutes_floor(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    minute = (ts.minute // 10) * 10
    return ts.replace(minute=minute, second=0, microsecond=0)


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
    # Display geometry traced against the current AMap basemap for surveyed roads.
    override_file = Path(__file__).with_name("map_route_overrides.json")
    overrides = json.loads(override_file.read_text(encoding="utf-8"))

    row_idx = 0
    while row_idx < len(df) - 1:
        row = df.iloc[row_idx]
        site_name_raw = row.iloc[0]
        if isinstance(site_name_raw, str) and site_name_raw.strip():
            site_name = _normalize_road_name(site_name_raw.strip())
            next_row = df.iloc[row_idx + 1]
            for col_idx, header in enumerate(row.tolist()):
                if header not in direction_names:
                    continue
                raw_path = next_row.iloc[col_idx]
                if not isinstance(raw_path, str) or not raw_path.strip():
                    continue
                points = [_parse_point(part.strip()) for part in raw_path.split(";")]
                survey_path = overrides.get(site_name, {}).get(header)
                clean_points = survey_path or _clean_path([pt for pt in points if pt is not None])
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


@ttl_cache(seconds=300, maxsize=1)
def _direction_flow_thresholds() -> Dict[Tuple[str, str], Tuple[float, float]]:
    # Use fixed historical reference distributions, independent of the window
    # currently selected in either client. Counts include all turning movements.
    stmt = select(
        TrafficData.site_name, TrafficData.approach, TrafficData.timestamp,
        func.sum(TrafficData.vehicle_count).label("flow"),
    ).group_by(TrafficData.site_name, TrafficData.approach, TrafficData.timestamp)
    with SessionLocal() as session:
        rows = session.execute(stmt).all()
    if not rows:
        return {}
    frame = pd.DataFrame(rows, columns=["site", "direction", "timestamp", "flow"])
    quantiles = frame.groupby(["site", "direction"])["flow"].quantile([0.5, 0.8]).unstack()
    return {key: (float(row[0.5]), float(row[0.8])) for key, row in quantiles.iterrows()}


def get_direction_flows(db: Session, start_time: str, end_time: str) -> List[DirectionFlow]:
    start, end = normalize_ts(start_time), normalize_ts(end_time)
    if start is None or end is None or start >= end:
        raise ValueError("start_time must be earlier than end_time")
    buckets = select(
        TrafficData.site_name.label("site"), TrafficData.approach.label("direction"),
        TrafficData.timestamp, func.sum(TrafficData.vehicle_count).label("flow"),
    ).where(TrafficData.timestamp >= start, TrafficData.timestamp < end).group_by(
        TrafficData.site_name, TrafficData.approach, TrafficData.timestamp,
    ).subquery()
    averages = db.execute(select(
        buckets.c.site, buckets.c.direction, func.avg(buckets.c.flow),
    ).group_by(buckets.c.site, buckets.c.direction)).all()
    thresholds = _direction_flow_thresholds()
    result = []
    for site, direction, count in averages:
        medium, high = thresholds[(site, direction)]
        level = "low"
        if count > 0 and count >= high:
            level = "high"
        elif count > 0 and count >= medium:
            level = "medium"
        result.append(DirectionFlow(
            site_name=site, direction=direction, average_count=count,
            medium_threshold=medium, high_threshold=high, level=level,
        ))
    return result


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
    road_name = _normalize_road_name(road_name)
    return _get_traffic_data_cached(time_range, start_time, end_time, road_name, direction, limit)


_DIRECTION_NAMES = {"E2W": "东向西", "W2E": "西向东", "N2S": "北向南", "S2N": "南向北"}


def _version_exists(version: str) -> None:
    with SessionLocal() as db:
        found = db.execute(text("SELECT 1 FROM traffic_dataset_version WHERE version_id=:v"), {"v": version}).first()
    if not found:
        raise ValueError(f"Unknown dataset version: {version}")


def get_versioned_traffic_data(version: str, start_time: Optional[str], end_time: Optional[str],
                               road_name: Optional[str], direction: Optional[str], limit: int) -> List[TrafficRecord]:
    _version_exists(version)
    sql = "SELECT timestamp, site_name, direction, flow_5min, avg_speed_site_kmh, weather, tmax_c, tmin_c FROM traffic_approach_observation WHERE dataset_version=:v"
    params: dict = {"v": version}
    if start_time:
        sql += " AND timestamp >= :start"; params["start"] = normalize_ts(start_time).isoformat(sep=" ")
    if end_time:
        sql += " AND timestamp <= :end"; params["end"] = normalize_ts(end_time).isoformat(sep=" ")
    if road_name:
        sql += " AND site_name=:site"; params["site"] = _normalize_road_name(road_name)
    if direction:
        sql += " AND direction=:direction"; params["direction"] = next((k for k, v in _DIRECTION_NAMES.items() if v == direction), direction)
    sql += " ORDER BY timestamp DESC LIMIT :limit"; params["limit"] = limit
    with SessionLocal() as db:
        rows = db.execute(text(sql), params).mappings().all()
    return [TrafficRecord(timestamp=r["timestamp"], site_name=r["site_name"], approach=_DIRECTION_NAMES.get(r["direction"], r["direction"]), movement="方向汇总", vehicle_count=max(0, int(r["flow_5min"] or 0)), avg_speed=max(0, float(r["avg_speed_site_kmh"] or 0)), weather=r["weather"] or "未知", tmax=int(r["tmax_c"] or 0), tmin=int(r["tmin_c"] or 0)) for r in rows]


def get_versioned_analysis_data(version: str, start_time: str, end_time: str, interval_minutes: int,
                                road_name: Optional[str], direction: Optional[str]) -> List[AnalysisPoint]:
    _version_exists(version)
    start_ts, end_ts = normalize_ts(start_time), normalize_ts(end_time)
    allowed = (10, 20, 30, 60, 120, 180, 240, 360, 720, 1440)
    if not start_ts or not end_ts or end_ts < start_ts or interval_minutes not in allowed:
        raise ValueError("Invalid time range or interval")
    sql = "SELECT timestamp, flow_5min, avg_speed_site_kmh FROM traffic_approach_observation WHERE dataset_version=:v AND timestamp>=:start AND timestamp<=:end AND data_valid_flag=1"
    params: dict = {"v": version, "start": start_ts.isoformat(sep=" "), "end": end_ts.isoformat(sep=" ")}
    if road_name:
        sql += " AND site_name=:site"; params["site"] = _normalize_road_name(road_name)
    if direction:
        sql += " AND direction=:direction"; params["direction"] = next((k for k, v in _DIRECTION_NAMES.items() if v == direction), direction)
    with SessionLocal() as db:
        rows = db.execute(text(sql), params).mappings().all()
    grouped: Dict[datetime, List[Tuple[float, float]]] = {}
    for r in rows:
        ts = datetime.fromisoformat(r["timestamp"])
        bucket = start_ts + timedelta(minutes=(int((ts-start_ts).total_seconds()//60)//interval_minutes)*interval_minutes)
        grouped.setdefault(bucket.replace(second=0,microsecond=0), []).append((float(r["flow_5min"] or 0), float(r["avg_speed_site_kmh"] or 0)))
    return [AnalysisPoint(bucket_start=b, vehicle_count=round(sum(x[0] for x in vals)/len(vals),2), avg_speed=round(sum(x[1] for x in vals)/len(vals),2)) for b,vals in sorted(grouped.items())]


def get_versioned_data_range(version: str) -> DataTimeRange:
    _version_exists(version)
    with SessionLocal() as db:
        row = db.execute(text("SELECT MIN(timestamp), MAX(timestamp) FROM traffic_approach_observation WHERE dataset_version=:v"), {"v":version}).first()
    if not row or not row[0] or not row[1]:
        raise ValueError("No traffic data found for version")
    return DataTimeRange(min_timestamp=row[0], max_timestamp=row[1])


def get_versioned_direction_flows(version: str, start_time: str, end_time: str) -> List[DirectionFlow]:
    _version_exists(version)
    start, end = normalize_ts(start_time), normalize_ts(end_time)
    if start is None or end is None or start >= end:
        raise ValueError("start_time must be earlier than end_time")
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT site_name, direction, timestamp, flow_5min FROM traffic_approach_observation "
            "WHERE dataset_version=:v AND data_valid_flag=1"
        ), {"v": version}).mappings().all()
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    quantiles = frame.groupby(["site_name", "direction"])["flow_5min"].quantile([0.5, 0.8]).unstack()
    selected = frame[(frame["timestamp"] >= start.isoformat(sep=" ")) & (frame["timestamp"] < end.isoformat(sep=" "))]
    averages = selected.groupby(["site_name", "direction"])["flow_5min"].mean()
    result = []
    for (site, direction), count in averages.items():
        if (site, direction) not in quantiles.index:
            continue
        medium, high = float(quantiles.loc[(site, direction), 0.5]), float(quantiles.loc[(site, direction), 0.8])
        level = "high" if count >= high and count > 0 else "medium" if count >= medium and count > 0 else "low"
        result.append(DirectionFlow(site_name=site, direction=_DIRECTION_NAMES.get(direction, direction), average_count=float(count), medium_threshold=medium, high_threshold=high, level=level))
    return result


@ttl_cache(seconds=300, maxsize=512)
def _get_traffic_data_cached(
    time_range: Optional[str],
    start_time: Optional[str],
    end_time: Optional[str],
    road_name: Optional[str],
    direction: Optional[str],
    limit: int,
) -> List[TrafficRecord]:
    db = SessionLocal()
    try:
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
    finally:
        db.close()


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
    ts = align_to_ten_minutes_floor(normalize_ts(time_range))
    road_name = _normalize_road_name(road_name)
    if not road_name:
        raise ValueError("road_name is required for prediction.")
    if not direction:
        raise ValueError("direction is required for prediction.")

    predicted = _load_or_generate_prediction_rows(
        db=db,
        road_name=road_name,
        direction=direction,
        as_of=ts,
    )
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


def get_versioned_prediction_data(version: str, time_range: Optional[str], road_name: Optional[str], direction: Optional[str]) -> List[PredictionRecord]:
    from ml.versioned_inference import predict_versioned
    _version_exists(version)
    ts = align_to_ten_minutes_floor(normalize_ts(time_range))
    site = _normalize_road_name(road_name)
    if not site or not direction:
        raise ValueError("road_name and direction are required for prediction.")
    code = next((k for k, v in _DIRECTION_NAMES.items() if v == direction), direction)
    predicted = predict_versioned(version, site, code, ts.strftime("%Y-%m-%d %H:%M:%S") if ts else None)
    return [PredictionRecord(timestamp=t, site_name=site, approach=direction, predicted_vehicle_count=n) for t,n in predicted]


def get_versioned_prediction_compare(version: str, time_range: str, road_name: str, direction: str) -> PredictionCompareSummary:
    predicted = get_versioned_prediction_data(version, time_range, road_name, direction)
    if not predicted:
        raise ValueError("Prediction model is not ready or no history is available for this road and direction.")
    code = next((k for k, v in _DIRECTION_NAMES.items() if v == direction), direction)
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT timestamp, flow_5min FROM traffic_approach_observation WHERE dataset_version=:v "
            "AND site_name=:site AND direction=:direction AND data_valid_flag=1 "
            "AND timestamp>=:start AND timestamp<=:end"
        ), {"v":version, "site":_normalize_road_name(road_name), "direction":code,
            "start":predicted[0].timestamp.strftime("%Y-%m-%d %H:%M:%S"), "end":predicted[-1].timestamp.strftime("%Y-%m-%d %H:%M:%S")}).mappings().all()
    actual = {datetime.fromisoformat(r["timestamp"]): int(r["flow_5min"] or 0) for r in rows}
    points=[]; errors=[]; sq=[]; ape=[]
    for p in predicted:
        if p.timestamp not in actual:
            continue
        err=abs(p.predicted_vehicle_count-actual[p.timestamp]); errors.append(err); sq.append(err**2)
        if actual[p.timestamp]: ape.append(err/actual[p.timestamp]*100)
        points.append(PredictionComparePoint(timestamp=p.timestamp, predicted_vehicle_count=p.predicted_vehicle_count, actual_vehicle_count=actual[p.timestamp], abs_error=err))
    if not points:
        raise ValueError("No actual observations overlap the prediction horizon.")
    return PredictionCompareSummary(mae=sum(errors)/len(errors), rmse=(sum(sq)/len(sq))**0.5, mape=sum(ape)/len(ape) if ape else 0, points=points)


@ttl_cache(seconds=300, maxsize=512)
def _get_prediction_data_cached(
    road_name: str,
    direction: str,
    as_of_key: Optional[str],
) -> List[Tuple[datetime, int]]:
    ts = datetime.strptime(as_of_key, "%Y-%m-%d %H:%M:%S") if as_of_key else None
    return predict_next_hour(site_name=road_name, approach=direction, as_of=ts)


def _load_or_generate_prediction_rows(
    db: Session,
    road_name: str,
    direction: str,
    as_of: Optional[datetime],
) -> List[Tuple[datetime, int]]:
    model_type = get_prediction_meta().get("model_type", "unavailable")
    resolved_as_of = _resolve_prediction_anchor_time(db, road_name, direction, as_of)
    if not resolved_as_of:
        return []

    if model_type != "unavailable":
        stored = _find_prediction_result_rows(db, road_name, direction, model_type, resolved_as_of)
        if stored:
            return stored

    as_of_key = resolved_as_of.strftime("%Y-%m-%d %H:%M:%S")
    predicted = _get_prediction_data_cached(road_name, direction, as_of_key)
    if predicted and model_type != "unavailable":
        _store_prediction_result_rows(db, road_name, direction, model_type, resolved_as_of, predicted)
    return predicted


def _resolve_prediction_anchor_time(
    db: Session,
    road_name: str,
    direction: str,
    as_of: Optional[datetime],
) -> Optional[datetime]:
    stmt = select(func.max(TrafficData.timestamp)).where(
        TrafficData.site_name == road_name,
        TrafficData.approach == direction,
    )
    if as_of:
        stmt = stmt.where(TrafficData.timestamp <= as_of)
    return db.execute(stmt).scalar_one_or_none()


def _find_prediction_result_rows(
    db: Session,
    road_name: str,
    direction: str,
    model_type: str,
    as_of: datetime,
) -> List[Tuple[datetime, int]]:
    rows = db.execute(
        select(PredictionResult)
        .where(
            PredictionResult.site_name == road_name,
            PredictionResult.approach == direction,
            PredictionResult.model_type == model_type,
            PredictionResult.as_of_time == as_of,
        )
        .order_by(PredictionResult.target_time.asc())
    ).scalars().all()
    if not rows:
        return []
    expected_start = as_of.replace(second=0, microsecond=0)
    expected_next = expected_start + timedelta(minutes=10)
    if rows[0].target_time not in {expected_start, expected_next}:
        return []
    return [(row.target_time, row.predicted_vehicle_count) for row in rows]


def _store_prediction_result_rows(
    db: Session,
    road_name: str,
    direction: str,
    model_type: str,
    as_of: datetime,
    predicted: List[Tuple[datetime, int]],
) -> None:
    db.execute(
        delete(PredictionResult).where(
            PredictionResult.site_name == road_name,
            PredictionResult.approach == direction,
            PredictionResult.model_type == model_type,
            PredictionResult.as_of_time == as_of,
        )
    )
    db.add_all(
        [
            PredictionResult(
                as_of_time=as_of,
                target_time=target_time,
                site_name=road_name,
                approach=direction,
                predicted_vehicle_count=int(count),
                model_type=model_type,
            )
            for target_time, count in predicted
        ]
    )
    db.commit()


@ttl_cache(seconds=1800, maxsize=1)
def get_prediction_meta() -> dict:
    return load_prediction_metadata()


def list_prediction_options(db: Session) -> List[PredictionOption]:
    return _list_prediction_options_cached()


def list_versioned_prediction_options(version: str) -> List[PredictionOption]:
    _version_exists(version)
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT DISTINCT site_name, direction FROM traffic_approach_observation "
            "WHERE dataset_version=:v ORDER BY site_name, direction"
        ), {"v":version}).all()
    grouped: Dict[str, List[str]] = {}
    for site, direction in rows:
        grouped.setdefault(site, []).append(_DIRECTION_NAMES.get(direction, direction))
    return [PredictionOption(site_name=site, directions=directions) for site,directions in grouped.items()]


@ttl_cache(seconds=1800, maxsize=1)
def _list_prediction_options_cached() -> List[PredictionOption]:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(
            select(TrafficData.site_name, TrafficData.approach)
            .distinct()
            .order_by(TrafficData.site_name.asc(), TrafficData.approach.asc())
        ).all()
    finally:
        db.close()

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
    ts = align_to_ten_minutes_floor(normalize_ts(time_range))
    if not ts:
        raise ValueError("time_range is required for prediction comparison.")
    if not road_name or not direction:
        raise ValueError("road_name and direction are required for prediction comparison.")

    predicted = _load_or_generate_prediction_rows(
        db=db,
        road_name=road_name,
        direction=direction,
        as_of=ts,
    )
    if not predicted:
        raise ValueError("Prediction model is not ready or no history is available for this road and direction.")

    cache_key = ts.strftime("%Y-%m-%d %H:%M:%S")
    actual_map = _get_prediction_actuals_cached(
        road_name,
        direction,
        cache_key,
        predicted[-1][0].strftime("%Y-%m-%d %H:%M:%S"),
    )
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


@ttl_cache(seconds=300, maxsize=512)
def _get_prediction_actuals_cached(
    road_name: str,
    direction: str,
    start_key: str,
    end_key: str,
) -> Dict[datetime, int]:
    start_ts = datetime.strptime(start_key, "%Y-%m-%d %H:%M:%S")
    end_ts = datetime.strptime(end_key, "%Y-%m-%d %H:%M:%S")
    db = SessionLocal()
    try:
        rows = db.execute(
            select(TrafficData)
            .where(
                TrafficData.site_name == road_name,
                TrafficData.approach == direction,
                TrafficData.timestamp >= start_ts,
                TrafficData.timestamp <= end_ts,
            )
            .order_by(TrafficData.timestamp.asc())
        ).scalars().all()
    finally:
        db.close()

    if not rows:
        return {}

    normalized_rows = [
        TrafficRecord(
            timestamp=row.timestamp,
            site_name=row.site_name,
            approach=row.approach,
            movement=row.movement,
            vehicle_count=int(row.vehicle_count),
            avg_speed=float(row.avg_speed),
            weather=str(row.weather),
            tmax=int(row.tmax),
            tmin=int(row.tmin),
        )
        for row in rows
    ]
    return _aggregate_actual_prediction_window(normalized_rows, start_ts)


def _aggregate_actual_prediction_window(rows: List[TrafficRecord], anchor_time: datetime) -> Dict[datetime, int]:
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
    window_start = anchor_time.replace(second=0, microsecond=0)
    window_end = window_start + timedelta(hours=1)
    return {
        bucket: int(round(item["sum"] / item["n"])) if item["n"] else 0
        for bucket, item in grouped.items()
        if window_start <= bucket < window_end
    }


def get_analysis_data(
    db: Session,
    start_time: str,
    end_time: str,
    interval_minutes: int,
    road_name: Optional[str],
    direction: Optional[str],
) -> List[AnalysisPoint]:
    road_name = _normalize_road_name(road_name)
    return _get_analysis_data_cached(start_time, end_time, interval_minutes, road_name, direction)


@ttl_cache(seconds=300, maxsize=512)
def _get_analysis_data_cached(
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
    if interval_minutes not in (10, 20, 30, 60, 120, 180, 240, 360, 720, 1440):
        raise ValueError("interval_minutes must be one of 10, 20, 30, 60, 120, 180, 240, 360, 720, 1440.")

    db = SessionLocal()
    try:
        stmt = select(TrafficData).where(
            and_(
                TrafficData.timestamp >= start_ts,
                TrafficData.timestamp <= end_ts,
            )
        )
        if road_name:
            stmt = stmt.where(TrafficData.site_name == road_name)
        if direction:
            stmt = stmt.where(TrafficData.approach == direction)

        rows = db.execute(stmt.order_by(TrafficData.timestamp.asc())).scalars().all()
    finally:
        db.close()

    if not rows:
        return []

    grouped: Dict[datetime, Dict[str, float]] = {}
    for row in rows:
        elapsed_minutes = int((row.timestamp - start_ts).total_seconds() // 60)
        bucket_offset = (elapsed_minutes // interval_minutes) * interval_minutes
        bucket = start_ts + timedelta(minutes=bucket_offset)
        bucket = bucket.replace(second=0, microsecond=0)
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
    return _get_data_time_range_cached()


@ttl_cache(seconds=1800, maxsize=1)
def _get_data_time_range_cached() -> DataTimeRange:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        row = db.execute(select(func.min(TrafficData.timestamp), func.max(TrafficData.timestamp))).first()
    finally:
        db.close()

    if not row or not row[0] or not row[1]:
        raise ValueError("No traffic data found.")
    return DataTimeRange(
        min_timestamp=row[0],
        max_timestamp=row[1],
    )


def get_direction_routes() -> List[DirectionRoute]:
    return load_direction_routes()


def _cleanup_user_alerts(now: Optional[datetime] = None) -> None:
    current = now or datetime.utcnow()
    threshold = current - _USER_ALERT_TTL
    _user_alerts[:] = [item for item in _user_alerts if item.created_at >= threshold]
    if len(_user_alerts) > _USER_ALERT_MAX:
        del _user_alerts[:-_USER_ALERT_MAX]


def create_user_alert(payload: UserAlertCreate) -> UserAlert:
    global _user_alert_seq

    with _user_alert_lock:
        _cleanup_user_alerts()
        _user_alert_seq += 1
        alert = UserAlert(
            id=_user_alert_seq,
            source=payload.source,
            title=payload.title,
            message=payload.message,
            site_name=payload.site_name,
            direction=payload.direction,
            start_time=payload.start_time,
            end_time=payload.end_time,
            action_label=payload.action_label,
            created_at=datetime.utcnow(),
        )
        _user_alerts.append(alert)
        return alert


def list_user_alerts(after_id: int = 0) -> List[UserAlert]:
    with _user_alert_lock:
        _cleanup_user_alerts()
        return [item for item in _user_alerts if item.id > after_id]
