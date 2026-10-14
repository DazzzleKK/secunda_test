"""Application settings loaded from the environment."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Final

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

RetryDelays = Annotated[tuple[int, ...], NoDecode]

DEV_ENVIRONMENTS: Final[frozenset[str]] = frozenset({"local", "test", "ci"})
INSECURE_SECRETS: Final[frozenset[str]] = frozenset(
    {"change-me", "local-dev-api-key", "local-dev-signing-secret"}
)


class Settings(BaseSettings):
    """Every knob of the service; values come from the environment or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "local"
    log_level: str = "INFO"
    api_key: str = Field(min_length=1)

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "payments"
    postgres_password: str = "payments"
    postgres_db: str = "payments"
    database_url: str | None = None

    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "guest"
    rabbitmq_password: str = "guest"
    rabbitmq_vhost: str = "/"

    outbox_batch_size: int = 100
    outbox_poll_interval_ms: int = 1000

    gateway_success_rate: float = 0.9
    gateway_min_delay_ms: int = 2000
    gateway_max_delay_ms: int = 5000
    gateway_random_seed: int | None = 42

    retry_delays_ms: RetryDelays = (5000, 25000)

    webhook_timeout_seconds: float = 5.0
    webhook_signing_secret: str = "change-me"

    @field_validator("retry_delays_ms", mode="before")
    @classmethod
    def _parse_retry_delays(cls, value: object) -> object:
        """Accept `5000,25000,125000` as well as a real sequence."""
        if isinstance(value, str):
            return tuple(int(part.strip()) for part in value.split(",") if part.strip())
        return value

    @property
    def sqlalchemy_url(self) -> str:
        """Async SQLAlchemy URL, built from parts when DATABASE_URL is absent."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def rabbitmq_url(self) -> str:
        """AMQP URL built from the separate RabbitMQ settings."""
        vhost = self.rabbitmq_vhost.lstrip("/")
        return (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}/{vhost}"
        )

    @property
    def outbox_poll_interval_seconds(self) -> float:
        """Poll interval of the outbox relay in seconds."""
        return self.outbox_poll_interval_ms / 1000

    @model_validator(mode="after")
    def _refuse_development_secrets(self) -> Settings:
        """Outside development the example secrets must be replaced.

        The defaults exist so that `docker compose up` works on a clean checkout;
        starting a non-local environment with them would silently accept a publicly
        known API key and sign webhooks with a known secret.
        """
        if self.app_env in DEV_ENVIRONMENTS:
            return self
        insecure = {self.api_key, self.webhook_signing_secret} & INSECURE_SECRETS
        if insecure:
            raise ValueError(
                f"replace the example secrets before running with APP_ENV={self.app_env!r}"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    `api_key` is required: mypy cannot see that pydantic-settings fills it from the
    environment, hence the narrow ignore.
    """
    return Settings()  # type: ignore[call-arg]


__all__ = ["Settings", "get_settings"]
