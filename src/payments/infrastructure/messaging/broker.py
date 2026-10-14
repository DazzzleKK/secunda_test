"""FastStream broker factory."""

from __future__ import annotations

from typing import Final

from faststream.rabbit import Channel, RabbitBroker

GRACEFUL_TIMEOUT_SECONDS: Final[float] = 15.0
RECONNECT_INTERVAL_SECONDS: Final[float] = 5.0

PUBLISHER_CHANNEL: Final[Channel] = Channel(
    publisher_confirms=True,
    on_return_raises=True,
)
"""Publishing channel of every process.

`on_return_raises=True` is a correctness requirement, not a preference: without it
a message that no queue is bound to is dropped by the exchange while the publisher
confirm still succeeds, so the relay would stamp `published_at` on an event nobody
will ever process. With it the unroutable message raises, the relay records a
transient failure and the event stays in the outbox for the next tick.
"""


def build_broker(amqp_url: str) -> RabbitBroker:
    """Create the broker used by one process.

    Publisher confirms are on: `publish` returns after the broker acknowledged the
    message, which is what lets the relay stamp `published_at` only for messages
    that really reached RabbitMQ.

    `fail_fast=False` is deliberate: the API must keep accepting payments while the
    broker is down — that is exactly what the outbox is for. The relay records the
    publication failure and retries on its next tick.
    """
    return RabbitBroker(
        amqp_url,
        graceful_timeout=GRACEFUL_TIMEOUT_SECONDS,
        fail_fast=False,
        reconnect_interval=RECONNECT_INTERVAL_SECONDS,
        default_channel=PUBLISHER_CHANNEL,
    )


__all__ = ["PUBLISHER_CHANNEL", "build_broker"]
