"""Webhook sender port."""

from __future__ import annotations

from typing import Protocol

from payments.application.dto.webhook_delivery_result import WebhookDeliveryResult
from payments.application.dto.webhook_payload import WebhookPayload
from payments.domain.value_objects import WebhookTarget


class WebhookSenderProtocol(Protocol):
    """Sends one webhook request to the merchant."""

    async def send(
        self,
        target: WebhookTarget,
        payload: WebhookPayload,
        *,
        attempt: int,
    ) -> WebhookDeliveryResult:
        """Deliver the payload; never raise for an HTTP error status."""
        ...


__all__ = ["WebhookSenderProtocol"]
