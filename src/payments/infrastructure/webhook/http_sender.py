"""Webhook delivery over HTTP.

An HTTP error status is a normal result here, not an exception: the retry policy
belongs to the consumer, which reads `WebhookDeliveryResult`. Only unexpected
programming errors may escape.
"""

from __future__ import annotations

import json
from typing import Any, Final

import httpx

from payments.application.dto.webhook_delivery_result import WebhookDeliveryResult
from payments.application.dto.webhook_payload import WebhookPayload
from payments.application.ports.webhook_sender import WebhookSenderProtocol
from payments.domain.value_objects import WebhookTarget
from payments.infrastructure.webhook.signer import (
    ATTEMPT_HEADER,
    REQUEST_ID_HEADER,
    SIGNATURE_HEADER,
    sign_payload,
)

CONTENT_TYPE_HEADER: Final[str] = "Content-Type"
JSON_CONTENT_TYPE: Final[str] = "application/json"
DEFAULT_TIMEOUT_SECONDS: Final[float] = 5.0
MAX_ERROR_LENGTH: Final[int] = 200


def payload_to_document(payload: WebhookPayload) -> dict[str, Any]:
    """Render the payload as JSON-ready data (money stays a string)."""
    return {
        "event_id": str(payload.event_id),
        "event": payload.event,
        "payment_id": str(payload.payment_id),
        "status": payload.status,
        "amount": payload.amount,
        "currency": payload.currency,
        "occurred_at": payload.occurred_at.isoformat(),
    }


def payload_to_body(payload: WebhookPayload) -> bytes:
    """Serialise the payload deterministically: the signature covers these bytes."""
    return json.dumps(
        payload_to_document(payload),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class HttpWebhookSender(WebhookSenderProtocol):
    """Posts webhook bodies with httpx and signs them with the shared secret."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        secret: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._secret = secret
        self._timeout = timeout_seconds

    async def send(
        self,
        target: WebhookTarget,
        payload: WebhookPayload,
        *,
        attempt: int,
    ) -> WebhookDeliveryResult:
        """Deliver one webhook attempt and describe what happened."""
        body = payload_to_body(payload)
        headers = {
            CONTENT_TYPE_HEADER: JSON_CONTENT_TYPE,
            REQUEST_ID_HEADER: str(payload.event_id),
            ATTEMPT_HEADER: str(attempt),
            SIGNATURE_HEADER: sign_payload(self._secret, body),
        }
        try:
            response = await self._client.post(
                target.url,
                content=body,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            return WebhookDeliveryResult(
                delivered=False,
                status_code=None,
                error=_describe(exc),
            )

        delivered = 200 <= response.status_code < 300
        return WebhookDeliveryResult(
            delivered=delivered,
            status_code=response.status_code,
            error=None if delivered else f"HTTP {response.status_code}",
        )


def _describe(exc: httpx.HTTPError) -> str:
    """Short, safe description of a transport failure."""
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]


__all__ = ["HttpWebhookSender", "payload_to_body", "payload_to_document"]
