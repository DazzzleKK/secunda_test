"""Body sent to the merchant webhook."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class WebhookPayload:
    """The webhook body; money travels as a string."""

    event_id: UUID
    event: str
    payment_id: UUID
    status: str
    amount: str
    currency: str
    occurred_at: datetime


__all__ = ["WebhookPayload"]
