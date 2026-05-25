"""Application settings."""

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    """Application configuration."""

    APP_NAME: str = "Supplier Management Backend"

    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173,https://avo-supplier-management.azurewebsites.net/"

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

    @field_validator("ALLOWED_ORIGINS")
    @classmethod
    def parse_origins(cls, v: str):
        return [x.strip() for x in v.split(",") if x.strip()]


settings = Settings()
