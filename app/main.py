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
    IntersectionPoint,
    PredictionRecord,
    PublicConfig,
    TrafficRecord,
    WeatherData,
)
from app.service import (
    get_analysis_data,
    get_data_time_range,
    get_direction_routes,
    get_prediction_data,
    get_traffic_data,
    get_weather,
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


@app.get("/api/traffic", response_model=List[TrafficRecord])
def traffic(
    time_range: Optional[str] = Query(default=None, description="YYYY-MM-DD HH:MM[:SS]"),
    start_time: Optional[str] = Query(default=None, description="YYYY-MM-DD HH:MM[:SS]"),
    end_time: Optional[str] = Query(default=None, description="YYYY-MM-DD HH:MM[:SS]"),
    road_name: Optional[str] = Query(default=None),
    direction: Optional[str] = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> List[TrafficRecord]:
    try:
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
    db: Session = Depends(get_db),
) -> List[PredictionRecord]:
    try:
        return get_prediction_data(db=db, time_range=time_range, road_name=road_name)
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
    db: Session = Depends(get_db),
) -> List[AnalysisPoint]:
    try:
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
def data_range(db: Session = Depends(get_db)) -> DataTimeRange:
    try:
        return get_data_time_range(db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/direction-routes", response_model=List[DirectionRoute])
def direction_routes() -> List[DirectionRoute]:
    return get_direction_routes()
