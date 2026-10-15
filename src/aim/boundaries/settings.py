"""The one env reader. Same DB_* names as AI-Pipeline-V3's `.env`, so both services can
share one env file on a host."""

from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # "ignore": a shared .env carries keys for other consumers (MLflow, RabbitMQ, ...).
    model_config = SettingsConfigDict(extra="ignore")

    db_host: str
    db_port: int = 3306
    db_username: str
    db_password: str
    data_db: str = "circly_data_handling"

    poll_interval_seconds: float = Field(default=30.0, gt=0)
    stale_grace_seconds: int = Field(default=3600, gt=0)

    @property
    def db_url(self) -> str:
        return (
            f"mysql+pymysql://{quote_plus(self.db_username)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.data_db}?charset=utf8mb4"
        )
