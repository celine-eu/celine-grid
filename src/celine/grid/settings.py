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
        # From the environment when set; the local realm's identity otherwise.
        # Literal constructor arguments would override CELINE_OIDC_* and leave the
        # client unconfigurable per deployment.
        audience=os.getenv("CELINE_OIDC_AUDIENCE", "svc-grid"),
        client_id=os.getenv("CELINE_OIDC_CLIENT_ID", "svc-grid"),
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
    mqtt_startup_timeout_seconds: float = 30.0

    # OPA policy engine — CELINE_POLICIES_DIR overrides the directory
    policies: PoliciesSettings = Field(default_factory=PoliciesSettings)

    # Kill switch for DSO alert dispatch (GRID_ALERTS_ENABLED). Off by default: a
    # completed grid run only dispatches nudges (and therefore e-mails) once enabled.
    grid_alerts_enabled: bool = False

    # Grid resilience pipeline flow name (as emitted by the DT pipeline)
    grid_pipeline_flow: str = "grid-resilience-flow"
    # The Prefect namespace celine-pipelines publishes the grid flow under
    # (celine-utils `get_namespace("grid")` → "grid" or "<base>.grid"). A completed run
    # under it evaluates the alert rules of every network that has one.
    grid_pipeline_namespace: str = "grid"
    # Forecast dates a run evaluates: run date .. run date + horizon - 1
    grid_alert_horizon_days: int = 3
    # Public URL of the grid UI, linked from the alert e-mail
    public_app_url: str = "http://grid.celine.localhost"


settings = Settings()
