"""FastStream broker factory."""

from __future__ import annotations

from typing import Final

from faststream.rabbit import RabbitBroker

GRACEFUL_TIMEOUT_SECONDS: Final[float] = 15.0
RECONNECT_INTERVAL_SECONDS: Final[float] = 5.0
TOPOLOGY_DECLARATION_ATTEMPTS: Final[int] = 5
TOPOLOGY_DECLARATION_PAUSE_SECONDS: Final[float] = 2.0


def build_broker(amqp_url: str) -> RabbitBroker:
    """Create the broker used by one process.

    Publisher confirms are on by default in FastStream: `publish` returns after the
    broker acknowledged the message, which is what lets the relay stamp
    `published_at` only for messages that really reached RabbitMQ.

    `fail_fast=False` is deliberate: the API must keep accepting payments while the
    broker is down — that is exactly what the outbox is for. The relay records the
    publication failure and retries on its next tick.
    """
    return RabbitBroker(
        amqp_url,
        graceful_timeout=GRACEFUL_TIMEOUT_SECONDS,
        fail_fast=False,
        reconnect_interval=RECONNECT_INTERVAL_SECONDS,
    )


__all__ = ["build_broker"]
