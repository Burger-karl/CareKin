# from pydantic_settings import BaseSettings, SettingsConfigDict


# class Settings(BaseSettings):
#     model_config = SettingsConfigDict(env_file=".env", extra="ignore")

#     ENVIRONMENT: str = "development"
#     SECRET_KEY: str = "change-me"
#     API_V1_PREFIX: str = "/api/v1"
#     ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

#     DATABASE_URL: str = "postgresql+asyncpg://carekin:carekin@localhost:5432/carekin"
#     REDIS_URL: str = "redis://localhost:6379/0"

#     FIELD_ENCRYPTION_KEY: str = ""


# settings = Settings()


"""Application settings.

Values come from environment variables, then a `.env` file, then the defaults below.
Defaults are safe for local development only. In staging and production the app
refuses to start if a secret is missing or still set to a development placeholder,
and it lists every problem at once so a misconfigured deploy fails fast and clearly.

Usage:
    from app.config import settings          # module-level singleton
    from app.config import get_settings      # for FastAPI Depends() or tests
"""

from __future__ import annotations

import base64
import binascii
import json
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


# Placeholder used only outside staging/production. Deployed environments reject it.
DEV_SECRET_KEY = "dev-only-insecure-secret-key-change-me"
MIN_SECRET_KEY_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        # Validation errors must never echo env values: they include secrets and end up in logs.
        hide_input_in_errors=True,
    )

    # ------------------------------------------------------------------ app
    PROJECT_NAME: str = "CareKin API"
    VERSION: str = "0.1.0"
    ENVIRONMENT: Environment = Environment.DEVELOPMENT
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"
    # Local time for reminders and schedules. Timestamps are always stored in UTC.
    TIMEZONE: str = "Africa/Lagos"

    # ------------------------------------------------------------- security
    SECRET_KEY: SecretStr = SecretStr(DEV_SECRET_KEY)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=1)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=14, ge=1)
    OTP_EXPIRE_SECONDS: int = Field(default=300, ge=60)
    OTP_MAX_ATTEMPTS: int = Field(default=5, ge=1)  # wrong guesses before a code is burned
    OTP_LENGTH: int = Field(default=6, ge=4, le=8)
    OTP_RESEND_COOLDOWN_SECONDS: int = Field(default=60, ge=0)  # per phone
    OTP_MAX_SENDS_PER_HOUR: int = Field(default=5, ge=1)  # per phone
    OTP_MAX_SENDS_PER_IP_PER_HOUR: int = Field(default=30, ge=1)  # per client IP
    # Version of the terms / privacy policy users consent to (ON-2). Bump it when the
    # policy changes; /auth/me then reports which consents need to be given again.
    CONSENT_POLICY_VERSION: str = "2026-10-v1"
    # Fernet key for field-level encryption of health data (PRD HP-4).
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    FIELD_ENCRYPTION_KEY: SecretStr | None = None
    # Comma-separated in .env, e.g. CORS_ORIGINS=http://localhost:3000,https://app.carekin.ng
    CORS_ORIGINS: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",  # Next.js web
        "http://localhost:8081",  # Expo dev server
    ]

    # ------------------------------------------------------------- database
    DATABASE_URL: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://carekin:carekin@localhost:5432/carekin"
    )
    DB_POOL_SIZE: int = Field(default=10, ge=1)
    DB_MAX_OVERFLOW: int = Field(default=20, ge=0)
    DB_POOL_TIMEOUT: int = Field(default=30, ge=1)  # seconds to wait for a free connection
    DB_POOL_RECYCLE: int = Field(default=1800, ge=60)  # seconds; avoids stale connections
    DB_STATEMENT_TIMEOUT_MS: int = Field(default=30_000, ge=0)  # 0 disables the timeout
    DB_ECHO: bool = False  # log every SQL statement

    # -------------------------------------------------------- redis / celery
    REDIS_URL: RedisDsn = RedisDsn("redis://localhost:6379/0")
    CELERY_BROKER_URL: str | None = None  # defaults to REDIS_URL
    CELERY_RESULT_BACKEND: str | None = None  # defaults to REDIS_URL

    # --------------------------------------------------------- integrations
    # True routes NIN, background checks, escrow, payments, SMS and WhatsApp to
    # deterministic mocks (build plan Day 3), so a late partner never blocks work.
    USE_MOCK_INTEGRATIONS: bool = True
    NIN_PROVIDER_API_KEY: SecretStr | None = None
    BACKGROUND_CHECK_API_KEY: SecretStr | None = None
    ESCROW_PARTNER_API_KEY: SecretStr | None = None
    PAYSTACK_SECRET_KEY: SecretStr | None = None
    STRIPE_SECRET_KEY: SecretStr | None = None
    STRIPE_WEBHOOK_SECRET: SecretStr | None = None
    SMS_PROVIDER_API_KEY: SecretStr | None = None
    SMS_SENDER_ID: str = "CareKin"
    WHATSAPP_ACCESS_TOKEN: SecretStr | None = None
    WHATSAPP_PHONE_NUMBER_ID: str | None = None
    WHATSAPP_VERIFY_TOKEN: SecretStr | None = None
    LLM_API_KEY: SecretStr | None = None

    # -------------------------------------------------------------- storage
    S3_BUCKET: str | None = None
    AWS_REGION: str = "af-south-1"
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: SecretStr | None = None

    # ----------------------------------------------------------- validators
    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _use_asyncpg_driver(cls, value: Any) -> Any:
        """Accept the plain URLs that hosts like Render and Heroku hand out.

        postgres://... and postgresql://... become postgresql+asyncpg://...
        """
        if isinstance(value, str):
            for prefix in ("postgres://", "postgresql://"):
                if value.startswith(prefix):
                    return "postgresql+asyncpg://" + value[len(prefix) :]
        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def _require_asyncpg(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must use the asyncpg driver (postgresql+asyncpg://...)")
        return value

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors(cls, value: Any) -> Any:
        """Accept a comma-separated string or a JSON list."""
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                return json.loads(value)
            return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("FIELD_ENCRYPTION_KEY")
    @classmethod
    def _valid_fernet_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value():
            return None
        try:
            raw = base64.urlsafe_b64decode(value.get_secret_value().encode())
        except (binascii.Error, ValueError) as exc:
            raise ValueError("FIELD_ENCRYPTION_KEY is not valid url-safe base64") from exc
        if len(raw) != 32:
            raise ValueError(
                "FIELD_ENCRYPTION_KEY must be a Fernet key (32 bytes, url-safe base64)"
            )
        return value

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(f"LOG_LEVEL {value!r} is not a valid logging level")
        return level

    @model_validator(mode="after")
    def _guard_deployed_environments(self) -> Settings:
        """Staging and production must not run with development secrets or settings."""
        if not self.is_deployed:
            return self
        problems: list[str] = []
        secret = self.SECRET_KEY.get_secret_value()
        if secret == DEV_SECRET_KEY:
            problems.append("SECRET_KEY is still the development placeholder")
        elif len(secret) < MIN_SECRET_KEY_LENGTH:
            problems.append(f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters")
        if self.FIELD_ENCRYPTION_KEY is None:
            problems.append("FIELD_ENCRYPTION_KEY is required to encrypt health data")
        if self.DEBUG:
            problems.append("DEBUG must be false")
        if "*" in self.CORS_ORIGINS:
            problems.append("CORS_ORIGINS must list explicit origins, not '*'")
        if problems:
            raise ValueError(
                f"Unsafe configuration for ENVIRONMENT={self.ENVIRONMENT.value}: "
                + "; ".join(problems)
            )
        return self

    # ------------------------------------------------------------ helpers
    @property
    def is_deployed(self) -> bool:
        return self.ENVIRONMENT in (Environment.STAGING, Environment.PRODUCTION)

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT is Environment.PRODUCTION

    @property
    def is_test(self) -> bool:
        return self.ENVIRONMENT is Environment.TEST

    @property
    def expose_otp_in_response(self) -> bool:
        """Return the OTP in API responses so web and mobile can test without a phone.

        Only ever true for local development and tests running on the mock SMS client.
        Staging and production never expose it, even with mocks switched on.
        """
        return (
            self.ENVIRONMENT in (Environment.DEVELOPMENT, Environment.TEST)
            and self.USE_MOCK_INTEGRATIONS
        )

    @property
    def database_url(self) -> str:
        """DATABASE_URL as a plain string, as SQLAlchemy expects."""
        return self.DATABASE_URL.unicode_string()

    @property
    def celery_broker_url(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL.unicode_string()

    @property
    def celery_result_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL.unicode_string()


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance. Call get_settings.cache_clear() in tests after changing env vars."""
    return Settings()


settings = get_settings()
