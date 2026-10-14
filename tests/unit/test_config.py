"""Configuration refuses to start with example secrets outside development."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from payments.infrastructure.config import Settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"_env_file": None, "api_key": "real-key"}
    values.update(overrides)
    return Settings(**values)


def test_a_local_run_accepts_the_example_secrets() -> None:
    settings = _settings(app_env="local", webhook_signing_secret="local-dev-signing-secret")

    assert settings.app_env == "local"


def test_an_empty_api_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        _settings(api_key="")


@pytest.mark.parametrize("app_env", ["production", "staging"])
def test_example_secrets_are_refused_outside_development(app_env: str) -> None:
    with pytest.raises(ValidationError):
        _settings(app_env=app_env, api_key="local-dev-api-key")


def test_real_secrets_are_accepted_outside_development() -> None:
    settings = _settings(
        app_env="production",
        api_key="a-real-key",
        webhook_signing_secret="a-real-secret",
    )

    assert settings.api_key == "a-real-key"


def test_retry_delays_are_parsed_from_a_comma_separated_string() -> None:
    settings = _settings(retry_delays_ms="1000,2000")

    assert settings.retry_delays_ms == (1000, 2000)
