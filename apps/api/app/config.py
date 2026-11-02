from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env.local", extra="ignore")

    database_url: str = "postgresql+asyncpg://dephgraph:dephgraph@localhost:5432/dephgraph"
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "dephgraph"
    redis_url: str = "redis://localhost:6379/0"
    supabase_jwt_secret: str = ""
    github_webhook_secret: str = ""
    internal_cron_secret: str = ""
    groq_api_key: str = ""
    environment: str = "development"
    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
