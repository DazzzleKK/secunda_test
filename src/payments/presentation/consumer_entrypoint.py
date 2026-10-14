"""Consumer process: listens to `payments.new` and handles every message.

The process is its own composition root: it owns one broker connection, one
database pool and one HTTP client, and it shuts all of them down gracefully when
Docker stops the container.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from types import FrameType

import httpx

from payments.application.use_cases.deliver_webhook import DeliverWebhookUseCase
from payments.application.use_cases.process_payment import ProcessPaymentUseCase
from payments.infrastructure.config import get_settings
from payments.infrastructure.db.session import build_engine, build_session_factory
from payments.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
from payments.infrastructure.gateway.fake_gateway import FakePaymentGateway
from payments.infrastructure.messaging.broker import build_broker
from payments.infrastructure.messaging.consumer import PaymentConsumer
from payments.infrastructure.messaging.topology import Topology, declare_topology
from payments.infrastructure.system import SystemClock
from payments.infrastructure.webhook.http_sender import HttpWebhookSender

logger = logging.getLogger(__name__)


async def main() -> None:
    """Run the consumer until the process is asked to stop."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    topology = Topology(retry_delays_ms=settings.retry_delays_ms)
    engine = build_engine(settings.sqlalchemy_url)
    session_factory = build_session_factory(engine)
    unit_of_work_factory = SqlAlchemyUnitOfWorkFactory(session_factory)
    clock = SystemClock()

    http_client = httpx.AsyncClient()
    broker = build_broker(settings.rabbitmq_url)
    consumer = PaymentConsumer(
        broker=broker,
        topology=topology,
        process_payment=ProcessPaymentUseCase(
            unit_of_work_factory,
            FakePaymentGateway(
                success_rate=settings.gateway_success_rate,
                min_delay_ms=settings.gateway_min_delay_ms,
                max_delay_ms=settings.gateway_max_delay_ms,
                seed=settings.gateway_random_seed,
            ),
            clock,
        ),
        deliver_webhook=DeliverWebhookUseCase(
            unit_of_work_factory,
            HttpWebhookSender(
                http_client,
                secret=settings.webhook_signing_secret,
                timeout_seconds=settings.webhook_timeout_seconds,
            ),
            clock,
        ),
    )
    consumer.register(broker)

    stop = asyncio.Event()
    _install_signal_handlers(stop)

    try:
        await declare_topology(settings.rabbitmq_url, topology)
        await broker.start()
        logger.info(
            "consumer started",
            extra={
                "retry_delays_ms": list(settings.retry_delays_ms),
                "max_attempts": topology.max_attempts,
            },
        )
        await stop.wait()
    finally:
        logger.info("consumer stopping")
        await broker.stop()
        await http_client.aclose()
        await engine.dispose()


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """Turn SIGTERM/SIGINT into a graceful shutdown."""

    def request_stop(signal_number: int, _: FrameType | None) -> None:
        logger.info("stop signal received", extra={"signal": signal_number})
        stop.set()

    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signal_number, request_stop, signal_number, None)
        except NotImplementedError:  # pragma: no cover - Windows fallback
            signal.signal(signal_number, request_stop)


if __name__ == "__main__":
    asyncio.run(main())
