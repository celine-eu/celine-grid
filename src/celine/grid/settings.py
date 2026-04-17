"""Application settings using pydantic-settings."""

import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from celine.sdk.settings.models import OidcSettings, MqttSettings, PoliciesSettings


class Settings(BaseSettings):
    """Application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    oidc: OidcSettings = OidcSettings(
        audience="svc-grid",
        client_id="svc-grid",
        client_secret=os.getenv("CELINE_OIDC_CLIENT_SECRET", "svc-grid"),
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8015

    # Database
    database_url: str = (
        "postgresql+asyncpg://postgres:securepassword123@host.docker.internal:15432/grid"
    )
    database_echo: bool = False

    # Security
    jwt_header_name: str = "x-auth-request-access-token"

    # CORS
    cors_origins: list[str] = ["http://localhost:3006"]

    # Upstream services
    digital_twin_api_url: Optional[str] = "http://host.docker.internal:8002"
    nudging_api_url: str = "http://host.docker.internal:8016"

    # Service-to-service OIDC scopes for outbound calls
    dt_client_scope: Optional[str] = None
    nudging_scope: Optional[str] = None

    # MQTT pipeline listener
    mqtt: MqttSettings = Field(default_factory=MqttSettings)

    # OPA policy engine — CELINE_POLICIES_DIR overrides the directory
    policies: PoliciesSettings = Field(default_factory=PoliciesSettings)

    # Grid resilience pipeline flow name (as emitted by the DT pipeline)
    grid_pipeline_flow: str = "grid-resilience-flow"


settings = Settings()
