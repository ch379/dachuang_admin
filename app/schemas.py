from datetime import datetime
from typing import List

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
