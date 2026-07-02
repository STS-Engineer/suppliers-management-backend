"""Application settings."""

import json

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    """Application configuration."""

    APP_NAME: str = "Supplier Management Backend"

    ALLOWED_ORIGINS: str = (
        "http://localhost:3000,"
        "http://localhost:5173,"
        "https://avo-supplier-management.azurewebsites.net"
    )
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    SMTP_HOST: str = ""
    SMTP_PORT: int = 25
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""

    SMTP_USE_STARTTLS: bool = False
    SMTP_USE_LOGIN: bool = False
    SMTP_TIMEOUT_SECONDS: int = 30

    EMAIL_MAX_RETRIES: int = 3
    EMAIL_RETRY_DELAY_SECONDS: int = 2

    SECRET_KEY: str = Field(
        validation_alias=AliasChoices(
            "SECRET_KEY",
            "JWT_SECRET_KEY",
        )
    )

    ALGORITHM: str = Field(
        default="HS256",
        validation_alias=AliasChoices(
            "ALGORITHM",
            "JWT_ALGORITHM",
        ),
    )

    DATABASE_URL: str = Field(
        validation_alias=AliasChoices(
            "DATABASE_URL",
            "database_url",
        )
    )

    AZURE_CONNECTION_STRING: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AZURE_CONNECTION_STRING",
            "azure_connection_string",
        ),
    )

    AZURE_STORAGE_CONTAINER_NAME: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AZURE_STORAGE_CONTAINER_NAME",
            "AZURE_CONTAINER_NAME",
            "azure_storage_container_name",
        ),
    )

    SQLALCHEMY_ECHO: bool = False

    FRONTEND_BASE_URL: str = (
        "https://avo-supplier-management.azurewebsites.net/"  # "http://localhost:5173"
    )

    # Auth flows
    OTP_EXPIRE_MINUTES: int = 15
    ACTIVATION_LINK_EXPIRE_HOURS: int = 48
    # Comma-separated list of access_profile values that may approve account requests.
    APPROVER_ROLES: str = (
        "purchasing_manager,purchasing_director,vp_conversion,supplier_owner"
    )

    ACTION_PLAN_API_URL: str = Field(
        default="https://sales-feedback.azurewebsites.net",
        validation_alias=AliasChoices("ACTION_PLAN_API_URL", "action_plan_api_url"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def database_url(self) -> str:
        """Normalize PostgreSQL async URL."""

        if self.DATABASE_URL.startswith("postgres://"):
            return self.DATABASE_URL.replace(
                "postgres://",
                "postgresql+asyncpg://",
                1,
            )

        if self.DATABASE_URL.startswith("postgresql://"):
            return self.DATABASE_URL.replace(
                "postgresql://",
                "postgresql+asyncpg://",
                1,
            )

        return self.DATABASE_URL

    @property
    def approver_roles(self) -> list[str]:
        return [r.strip() for r in self.APPROVER_ROLES.split(",") if r.strip()]

    @property
    def cors_origins(self) -> list[str]:
        """Return CORS origins from a simple comma-separated setting."""

        raw_value = self.ALLOWED_ORIGINS.strip()
        if not raw_value:
            return []

        if raw_value.startswith("["):
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                return []
            if isinstance(parsed, list):
                return [str(origin).strip() for origin in parsed if str(origin).strip()]
            return []

        return [origin.strip() for origin in raw_value.split(",") if origin.strip()]

    @property
    def frontend_base_url(self) -> str:
        """Return the frontend base URL without a trailing slash."""

        return self.FRONTEND_BASE_URL.rstrip("/")


settings = Settings()
