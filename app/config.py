from functools import lru_cache
from pathlib import Path
from typing import Dict, Tuple

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_name: str = "Traffic Monitoring System"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # Backward-compatible key name.
    amap_api_key: str = "eb9daa43cedc80f0df9368ef649f91c5"
    # JS API key for frontend map SDK.
    amap_js_api_key: str = "eb9daa43cedc80f0df9368ef649f91c5"
    # Web service key for backend REST geospatial services.
    amap_web_service_key: str = "d065ac1646d067ce314de53567ba4a71"
    weather_api_key: str = "replace_with_real_weather_api_key"

    database_url: str = "sqlite:///./traffic.db"
    database_password: str = "traffic_password"
    redis_url: str = "redis://127.0.0.1:6379/0"
    model_artifact_dir: str = "artifacts"
    best_model_metadata: str = "artifacts/best_model.json"

    cors_allow_origins: str = "*"

    key_intersections: Dict[str, Tuple[float, float]] = Field(
        default_factory=lambda: {
            "星峰路康田路交叉口": (106.502459, 29.639363),
            "星峰路交叉口": (106.506524, 29.640675),
            "星光大道北延申段": (106.50672, 29.640812),
            "恒福路": (106.507081, 29.637511),
        }
    )

    @property
    def effective_amap_js_key(self) -> str:
        return self.amap_js_api_key or self.amap_api_key

    @property
    def artifact_dir_path(self) -> Path:
        return Path(self.model_artifact_dir)

    @property
    def best_model_metadata_path(self) -> Path:
        return Path(self.best_model_metadata)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
