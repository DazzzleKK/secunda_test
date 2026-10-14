"""Static API key authentication for the HTTP endpoints."""

from __future__ import annotations

import secrets
from typing import Annotated

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import Header

from payments.infrastructure.config import Settings
from payments.presentation.api.exceptions import UnauthorizedError

API_KEY_HEADER = "X-API-Key"


def verify_api_key(provided: str | None, expected: str) -> None:
    """Compare the presented key with the configured one in constant time."""
    if not provided or not expected:
        # An empty configured key must never authenticate anybody.
        raise UnauthorizedError()
    if not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise UnauthorizedError()


@inject
async def require_api_key(
    settings: FromDishka[Settings],
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    """Guard every business endpoint of the service."""
    verify_api_key(x_api_key, settings.api_key)


__all__ = ["API_KEY_HEADER", "require_api_key", "verify_api_key"]
