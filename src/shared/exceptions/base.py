"""Base error hierarchy shared by all bounded contexts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar


class AppError(Exception):
    """Base class of every error raised by the application.

    Subclasses define a stable machine readable `code` and may add context fields.
    `details` is an explicit whitelist of context keys: it is what the HTTP layer
    is allowed to expose in the `ErrorEnvelope`.
    """

    code: ClassVar[str] = "app_error"
    default_message: ClassVar[str] = "Application error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details: dict[str, Any] = dict(details) if details is not None else {}
        super().__init__(self.message)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, "
            f"message={self.message!r}, details={self.details!r})"
        )
