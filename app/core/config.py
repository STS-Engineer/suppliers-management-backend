"""Application settings."""

from pydantic import AliasChoices, Field
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    """Application configuration."""

    APP_NAME: str = "Supplier Management Backend"

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


settings = Settings()