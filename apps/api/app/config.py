from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env.local", extra="ignore")

    database_url: str = "postgresql+asyncpg://dephgraph:dephgraph@localhost:5432/dephgraph"
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "dephgraph"
    redis_url: str = "redis://localhost:6379/0"
    supabase_url: str = ""  # JWKS source for ES256/RS256 tokens; also where the web app signs in
    supabase_anon_key: str = ""  # only the scripts use these two; the app never reads them
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""  # legacy HS256 projects
    github_webhook_secret: str = ""
    internal_cron_secret: str = ""
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-70b-versatile"
    github_app_id: str = ""
    github_private_key_base64: str = ""
    environment: str = "development"
    cors_origins: list[str] = ["http://localhost:3000"]
    rate_limit: str = "100/minute"  # per client IP, all routes except /health
    # Reverse proxies in front of the app (Render = 1). 0 = ignore X-Forwarded-For.
    trusted_proxy_hops: int = 1


settings = Settings()
