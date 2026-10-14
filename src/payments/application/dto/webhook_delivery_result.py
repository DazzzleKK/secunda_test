"""Output of the webhook sender port."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WebhookDeliveryResult:
    """The result of a single webhook delivery attempt."""

    delivered: bool
    status_code: int | None = None
    error: str | None = None


__all__ = ["WebhookDeliveryResult"]
