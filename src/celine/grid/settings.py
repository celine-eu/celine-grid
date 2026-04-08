"""Application settings using pydantic-settings."""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from celine.sdk.settings.models import OidcSettings


class Settings(BaseSettings):
    """Application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    oidc: OidcSettings = OidcSettings()

    # Server
    host: str = "0.0.0.0"
    port: int = 8015

    # Database
    database_url: str = (
        "postgresql+asyncpg://postgres:securepassword123@host.docker.internal:15432/celine_grid"
    )
    database_echo: bool = False

    # Security
    jwt_header_name: str = "x-auth-request-access-token"

    # CORS
    cors_origins: list[str] = ["http://localhost:3006"]

    # Upstream services
    digital_twin_api_url: Optional[str] = "http://host.docker.internal:8002"

    # Default network / entity ID used when the frontend doesn't specify one
    default_network_id: str = "default"


settings = Settings()
