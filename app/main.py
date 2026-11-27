from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, init_db
from app.schemas import (
    AnalysisPoint,
    DataTimeRange,
    DirectionRoute,
    DirectionFlow,
    IntersectionPoint,
    PredictionCompareSummary,
    PredictionMeta,
    PredictionOption,
    PredictionRecord,
    PublicConfig,
    TrafficRecord,
    UserAlert,
    UserAlertCreate,
    WeatherData,
)
from app.service import (
    create_user_alert,
    get_analysis_data,
    get_data_time_range,
    get_direction_routes,
    get_direction_flows,
    get_prediction_data,
    get_prediction_meta,
    get_prediction_compare,
    get_traffic_data,
    get_versioned_traffic_data,
    get_versioned_analysis_data,
    get_versioned_data_range,
    get_versioned_direction_flows,
    get_versioned_prediction_data,
    get_versioned_prediction_compare,
    list_versioned_prediction_options,
    get_weather,
    list_user_alerts,
    list_prediction_options,
    list_intersections,
)


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")

allow_origins = [item.strip() for item in settings.cors_allow_origins.split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins if allow_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "env": settings.app_env}


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/api/config/public", response_model=PublicConfig)
def get_public_config(db: Session = Depends(get_db)) -> PublicConfig:
    return PublicConfig(
        amap_api_key=settings.effective_amap_js_key,
        intersections=list_intersections(db),
    )


@app.get("/api/intersections", response_model=List[IntersectionPoint])
def intersections(db: Session = Depends(get_db)) -> List[IntersectionPoint]:
    return list_intersections(db)


@app.get("/api/dataset-versions")
def dataset_versions() -> list[dict]:
    from sqlalchemy import text
    from app.db import engine

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT version_id, display_name FROM traffic_dataset_version "
            "ORDER BY CASE WHEN version_id='旧版' THEN 0 ELSE 1 END, version_id"
        )).mappings().all()
    return [dict(row) for row in rows]


@app.get("/api/traffic", response_model=List[TrafficRecord])
def traffic(
    time_range: Optional[str] = Query(default=None, description="YYYY-MM-DD HH:MM[:SS]"),
    start_time: Optional[str] = Query(default=None, description="YYYY-MM-DD HH:MM[:SS]"),
    end_time: Optional[str] = Query(default=None, description="YYYY-MM-DD HH:MM[:SS]"),
    road_name: Optional[str] = Query(default=None),
    direction: Optional[str] = Query(default=None),
    dataset_version: str = Query(default="旧版"),
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> List[TrafficRecord]:
    try:
        if dataset_version != "旧版":
            return get_versioned_traffic_data(
                dataset_version, start_time, end_time, road_name, direction, limit
            )
        return get_traffic_data(
            db=db,
            time_range=time_range,
            start_time=start_time,
            end_time=end_time,
            road_name=road_name,
            direction=direction,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/prediction", response_model=List[PredictionRecord])
def prediction(
    time_range: Optional[str] = Query(default=None, description="YYYY-MM-DD HH:MM[:SS]"),
    road_name: Optional[str] = Query(default=None),
    direction: Optional[str] = Query(default=None),
    dataset_version: str = Query(default="旧版"),
    db: Session = Depends(get_db),
) -> List[PredictionRecord]:
    try:
        if dataset_version != "旧版":
            return get_versioned_prediction_data(dataset_version, time_range, road_name, direction)
        return get_prediction_data(db=db, time_range=time_range, road_name=road_name, direction=direction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/prediction/meta", response_model=PredictionMeta)
def prediction_meta(dataset_version: str = Query(default="旧版")) -> PredictionMeta:
    if dataset_version != "旧版":
        from ml.versioned_inference import get_version_model_meta
        return PredictionMeta(**get_version_model_meta(dataset_version))
    return PredictionMeta(**get_prediction_meta())


@app.get("/api/prediction/options", response_model=List[PredictionOption])
def prediction_options(dataset_version: str = Query(default="旧版"), db: Session = Depends(get_db)) -> List[PredictionOption]:
    if dataset_version != "旧版":
        return list_versioned_prediction_options(dataset_version)
    return list_prediction_options(db)


@app.get("/api/prediction/compare", response_model=PredictionCompareSummary)
def prediction_compare(
    time_range: str = Query(description="YYYY-MM-DD HH:MM[:SS]"),
    road_name: str = Query(),
    direction: str = Query(),
    dataset_version: str = Query(default="旧版"),
    db: Session = Depends(get_db),
) -> PredictionCompareSummary:
    try:
        if dataset_version != "旧版":
            return get_versioned_prediction_compare(dataset_version, time_range, road_name, direction)
        return get_prediction_compare(db=db, time_range=time_range, road_name=road_name, direction=direction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/weather", response_model=WeatherData)
def weather(
    time_range: Optional[str] = Query(default=None, description="YYYY-MM-DD HH:MM[:SS]"),
    db: Session = Depends(get_db),
) -> WeatherData:
    try:
        return get_weather(db=db, time_range=time_range)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/analysis", response_model=List[AnalysisPoint])
def analysis(
    start_time: str = Query(description="YYYY-MM-DD HH:MM[:SS]"),
    end_time: str = Query(description="YYYY-MM-DD HH:MM[:SS]"),
    interval_minutes: int = Query(default=10),
    road_name: Optional[str] = Query(default=None),
    direction: Optional[str] = Query(default=None),
    dataset_version: str = Query(default="旧版"),
    db: Session = Depends(get_db),
) -> List[AnalysisPoint]:
    try:
        if dataset_version != "旧版":
            return get_versioned_analysis_data(
                dataset_version, start_time, end_time, interval_minutes, road_name, direction
            )
        return get_analysis_data(
            db=db,
            start_time=start_time,
            end_time=end_time,
            interval_minutes=interval_minutes,
            road_name=road_name,
            direction=direction,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/data-range", response_model=DataTimeRange)
def data_range(dataset_version: str = Query(default="旧版"), db: Session = Depends(get_db)) -> DataTimeRange:
    try:
        if dataset_version != "旧版":
            return get_versioned_data_range(dataset_version)
        return get_data_time_range(db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/direction-routes", response_model=List[DirectionRoute])
def direction_routes() -> List[DirectionRoute]:
    return get_direction_routes()


@app.get("/api/direction-flows", response_model=List[DirectionFlow])
def direction_flows(
    start_time: str, end_time: str, dataset_version: str = Query(default="旧版"), db: Session = Depends(get_db),
) -> List[DirectionFlow]:
    try:
        if dataset_version != "旧版":
            return get_versioned_direction_flows(dataset_version, start_time, end_time)
        return get_direction_flows(db, start_time, end_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/user-alerts", response_model=UserAlert)
def post_user_alert(payload: UserAlertCreate) -> UserAlert:
    return create_user_alert(payload)


@app.get("/api/user-alerts", response_model=List[UserAlert])
def get_user_alerts(after_id: int = Query(default=0, ge=0)) -> List[UserAlert]:
    return list_user_alerts(after_id=after_id)
