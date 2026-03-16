from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class IntersectionPoint(BaseModel):
    site_name: str
    longitude: float
    latitude: float


class TrafficRecord(BaseModel):
    timestamp: datetime
    site_name: str
    approach: str
    movement: str
    vehicle_count: int = Field(ge=0)
    avg_speed: float = Field(ge=0)
    weather: str
    tmax: int
    tmin: int


class PredictionRecord(BaseModel):
    timestamp: datetime
    site_name: str
    approach: str
    predicted_vehicle_count: int = Field(ge=0)


class PredictionMeta(BaseModel):
    model_name: str
    model_type: str
    valid_mae: Optional[float] = None
    test_mae: Optional[float] = None
    valid_rmse: Optional[float] = None
    test_rmse: Optional[float] = None
    valid_mape: Optional[float] = None
    test_mape: Optional[float] = None
    lookback_steps: Optional[int] = None
    forecast_steps: Optional[int] = None
    status: str = "ready"


class PredictionOption(BaseModel):
    site_name: str
    directions: List[str]


class PredictionComparePoint(BaseModel):
    timestamp: datetime
    predicted_vehicle_count: int = Field(ge=0)
    actual_vehicle_count: int = Field(ge=0)
    abs_error: int = Field(ge=0)


class PredictionCompareSummary(BaseModel):
    mae: float = Field(ge=0)
    rmse: float = Field(ge=0)
    mape: float = Field(ge=0)
    points: List[PredictionComparePoint]


class WeatherData(BaseModel):
    weather: str
    tmax: int
    tmin: int


class PublicConfig(BaseModel):
    amap_api_key: str
    intersections: List[IntersectionPoint]


class AnalysisPoint(BaseModel):
    bucket_start: datetime
    vehicle_count: float = Field(ge=0)
    avg_speed: float = Field(ge=0)


class DataTimeRange(BaseModel):
    min_timestamp: datetime
    max_timestamp: datetime


class RoutePoint(BaseModel):
    longitude: float
    latitude: float


class DirectionRoute(BaseModel):
    site_name: str
    direction: str
    path: List[RoutePoint]
