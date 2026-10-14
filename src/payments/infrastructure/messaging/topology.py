"""RabbitMQ topology: the only place that declares exchanges, queues and tiers.

The retry ladder is derived from configuration: `RETRY_DELAYS_MS` with N values
gives N retries, so a message is processed at most `N + 1` times before it lands in
`payments.dlq`. Every queue is durable, every tier is a queue with a fixed
`x-message-ttl` and a dead letter exchange back to the working queue — no `sleep`
in the handler and no per-message TTL (which would block the head of the queue).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import aio_pika
from faststream.rabbit import Channel, ExchangeType, RabbitExchange, RabbitQueue

EXCHANGE_NAME: Final[str] = "payments"
RETRY_EXCHANGE_NAME: Final[str] = "payments.retry"
DLX_EXCHANGE_NAME: Final[str] = "payments.dlx"
WORK_QUEUE_NAME: Final[str] = "payments.new"
WORK_ROUTING_KEY: Final[str] = "payments.new"
DLQ_NAME: Final[str] = "payments.dlq"
DLQ_ROUTING_KEY: Final[str] = "payments.dlq"
RETRY_QUEUE_TEMPLATE: Final[str] = "payments.retry.{tier}"
RETRY_ROUTING_KEY_TEMPLATE: Final[str] = "retry.{tier}"
DEFAULT_RETRY_DELAYS_MS: Final[tuple[int, ...]] = (5_000, 25_000)
PREFETCH_COUNT: Final[int] = 10

ATTEMPT_HEADER: Final[str] = "x-attempt"
ERROR_HEADER: Final[str] = "x-error"
FAILED_AT_HEADER: Final[str] = "x-failed-at"
EVENT_ID_HEADER: Final[str] = "x-event-id"
EVENT_TYPE_HEADER: Final[str] = "x-event-type"


@dataclass(frozen=True, slots=True)
class RetryTarget:
    """Where a failed message goes next: a retry tier with a fixed delay."""

    tier: int
    queue_name: str
    routing_key: str
    delay_ms: int


@dataclass(frozen=True, slots=True)
class Topology:
    """Names, declarations and the retry ladder of the service."""

    retry_delays_ms: tuple[int, ...] = DEFAULT_RETRY_DELAYS_MS
    prefetch_count: int = PREFETCH_COUNT

    def __post_init__(self) -> None:
        if not self.retry_delays_ms:
            raise ValueError("at least one retry delay is required")
        if any(delay <= 0 for delay in self.retry_delays_ms):
            raise ValueError("retry delays must be positive")
        if self.prefetch_count < 1:
            raise ValueError("prefetch_count must be positive")

    @property
    def max_attempts(self) -> int:
        """How many times a message may be processed before the DLQ."""
        return len(self.retry_delays_ms) + 1

    @property
    def retry_tiers(self) -> tuple[RetryTarget, ...]:
        """All retry tiers, numbered from one."""
        return tuple(
            RetryTarget(
                tier=tier,
                queue_name=RETRY_QUEUE_TEMPLATE.format(tier=tier),
                routing_key=RETRY_ROUTING_KEY_TEMPLATE.format(tier=tier),
                delay_ms=delay,
            )
            for tier, delay in enumerate(self.retry_delays_ms, start=1)
        )

    def retry_target(self, attempt: int) -> RetryTarget | None:
        """Target of the next try after a failed `attempt` (0-based).

        `None` means the attempts are exhausted and the message must be parked in
        the dead letter queue.
        """
        if attempt + 1 >= self.max_attempts:
            return None
        return self.retry_tiers[attempt]

    @property
    def exchange(self) -> RabbitExchange:
        """Topic exchange of the service."""
        return RabbitExchange(EXCHANGE_NAME, type=ExchangeType.TOPIC, durable=True)

    @property
    def retry_exchange(self) -> RabbitExchange:
        """Direct exchange fanning retries out to the tiers."""
        return RabbitExchange(RETRY_EXCHANGE_NAME, type=ExchangeType.DIRECT, durable=True)

    @property
    def dlx_exchange(self) -> RabbitExchange:
        """Direct exchange of the dead letter queue."""
        return RabbitExchange(DLX_EXCHANGE_NAME, type=ExchangeType.DIRECT, durable=True)

    @property
    def work_queue(self) -> RabbitQueue:
        """Working queue the consumer listens to."""
        return RabbitQueue(WORK_QUEUE_NAME, durable=True, routing_key=WORK_ROUTING_KEY)

    @property
    def channel(self) -> Channel:
        """Channel settings of the consumer (consumer prefetch)."""
        return Channel(prefetch_count=self.prefetch_count)


async def declare_topology(amqp_url: str, topology: Topology) -> None:
    """Declare every broker object; safe to run on every start.

    FastStream declares the working queue and the exchanges it publishes to itself,
    but the retry tiers and the dead letter queue have no consumer, so they are
    declared here, explicitly, with the arguments that make the ladder work.
    """
    connection = await aio_pika.connect_robust(amqp_url)
    try:
        channel = await connection.channel()
        try:
            exchange = await channel.declare_exchange(
                EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
            )
            retry_exchange = await channel.declare_exchange(
                RETRY_EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
            )
            dlx_exchange = await channel.declare_exchange(
                DLX_EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
            )

            work_queue = await channel.declare_queue(WORK_QUEUE_NAME, durable=True)
            await work_queue.bind(exchange, routing_key=WORK_ROUTING_KEY)

            dead_letter_queue = await channel.declare_queue(DLQ_NAME, durable=True)
            await dead_letter_queue.bind(dlx_exchange, routing_key=DLQ_ROUTING_KEY)

            for target in topology.retry_tiers:
                tier_queue = await channel.declare_queue(
                    target.queue_name,
                    durable=True,
                    arguments={
                        "x-message-ttl": target.delay_ms,
                        "x-dead-letter-exchange": EXCHANGE_NAME,
                        "x-dead-letter-routing-key": WORK_ROUTING_KEY,
                    },
                )
                await tier_queue.bind(retry_exchange, routing_key=target.routing_key)
        finally:
            await channel.close()
    finally:
        await connection.close()


__all__ = [
    "ATTEMPT_HEADER",
    "DLQ_NAME",
    "ERROR_HEADER",
    "EVENT_ID_HEADER",
    "EVENT_TYPE_HEADER",
    "FAILED_AT_HEADER",
    "RetryTarget",
    "Topology",
    "declare_topology",
]
