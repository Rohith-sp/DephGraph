from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_parents = Path(__file__).resolve().parents
ROOT = _parents[min(3, len(_parents) - 1)]  # repo root (.env.local); a shallow deploy path has none


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
    # Extra sign-in restriction: comma-separated email domains; empty = any verified email.
    allowed_email_domains: str = ""
    # Invite-only access: comma-separated emails of the organisation admins, the only people who may
    # create teams. Everyone else needs a team invite. Empty = open (development and CI only).
    org_admin_emails: str = ""
    github_webhook_secret: str = ""
    internal_cron_secret: str = ""
    groq_api_key: str = ""
    groq_model: str = (
        "openai/gpt-oss-120b"  # llama-3.x ids were retired by Groq (checked 22 Nov 2026)
    )
    github_app_id: str = ""
    github_private_key_base64: str = ""
    github_repository: str = ""  # owner/name; where cluster.yml is dispatched (as the GitHub App)
    web_url: str = "http://localhost:3000"  # dashboard base URL, linked from alert issues
    environment: str = "development"
    sentry_dsn: str = ""  # empty = no error reporting
    sentry_traces_sample_rate: float = 0.1
    cors_origins: list[str] = ["http://localhost:3000"]
    rate_limit: str = "100/minute"  # per client IP, all routes except /health
    # Reverse proxies in front of the app (Vercel overwrites X-Forwarded-For with the client IP = 1).
    # 0 = ignore X-Forwarded-For.
    trusted_proxy_hops: int = 1
    # Vercel sets VERCEL=1 in every deployment; true = serverless connection settings.
    serverless: bool = Field(default=False, validation_alias="VERCEL")

    @model_validator(mode="after")
    def production_needs_org_admins(self) -> "Settings":
        if self.environment == "production" and not self.org_admins:
            raise ValueError("ORG_ADMIN_EMAILS is required when ENVIRONMENT=production")
        return self

    @property
    def org_admins(self) -> set[str]:
        return {e.strip().lower() for e in self.org_admin_emails.split(",") if e.strip()}

    @property
    def email_domains(self) -> set[str]:
        return {
            d.strip().lower().lstrip("@")
            for d in self.allowed_email_domains.split(",")
            if d.strip()
        }


settings = Settings()
