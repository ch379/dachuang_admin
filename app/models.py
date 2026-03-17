from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TrafficData(Base):
    __tablename__ = "traffic_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    site_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    approach: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    movement: Mapped[str] = mapped_column(String(64), nullable=False)
    lane_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vehicle_count: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_speed: Mapped[float] = mapped_column(Float, nullable=False)
    weather: Mapped[str] = mapped_column(String(32), nullable=False)
    tmax: Mapped[int] = mapped_column(Integer, nullable=False)
    tmin: Mapped[int] = mapped_column(Integer, nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_sheet: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_traffic_timestamp_site_approach", "timestamp", "site_name", "approach"),
    )


class PredictionResult(Base):
    __tablename__ = "prediction_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    target_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    site_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    approach: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    predicted_vehicle_count: Mapped[int] = mapped_column(Integer, nullable=False)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index(
            "ix_prediction_lookup",
            "site_name",
            "approach",
            "model_type",
            "as_of_time",
            "target_time",
            unique=True,
        ),
    )
