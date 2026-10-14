"""Outbox relay: publishes what the use cases have already committed.

The relay is the only publisher of business events. It locks a batch of pending
rows with `FOR UPDATE SKIP LOCKED`, so any number of relays (embedded in the API,
or started as standalone processes) can work in parallel without sending the same
event twice on purpose.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress
from typing import Final

from payments.application.use_cases.publish_outbox import PublishOutboxUseCase
from payments.infrastructure.config import Settings, get_settings
from payments.infrastructure.db.session import build_engine, build_session_factory
from payments.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
from payments.infrastructure.messaging.broker import build_broker
from payments.infrastructure.messaging.publisher import RabbitEventPublisher
from payments.infrastructure.messaging.topology import (
    Topology,
    declare_topology_with_retry,
)
from payments.infrastructure.system import SystemClock

logger = logging.getLogger(__name__)

ERROR_RETRY_PAUSE_SECONDS: Final[float] = 5.0
STANDALONE_TOPOLOGY_ATTEMPTS: Final[int] = 3
STANDALONE_TOPOLOGY_PAUSE_SECONDS: Final[float] = 1.0


class OutboxRelay:
    """Publishes pending outbox events in a loop until asked to stop."""

    def __init__(self, use_case: PublishOutboxUseCase, settings: Settings) -> None:
        self._use_case = use_case
        self._settings = settings

    async def run_once(self) -> int:
        """Publish one batch and return how many events were published."""
        return await self._use_case.execute(batch_size=self._settings.outbox_batch_size)

    async def run(self, stop: asyncio.Event) -> None:
        """Poll the outbox until `stop` is set; a failure never kills the loop."""
        interval = self._settings.outbox_poll_interval_seconds
        while not stop.is_set():
            try:
                published = await self.run_once()
            except Exception:
                logger.exception("outbox relay iteration failed")
                await self._sleep(stop, ERROR_RETRY_PAUSE_SECONDS)
                continue
            if published:
                logger.info("published outbox events", extra={"count": published})
            await self._sleep(stop, interval)

    @staticmethod
    async def _sleep(stop: asyncio.Event, seconds: float) -> None:
        """Sleep, but wake up immediately when a stop is requested."""
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=seconds)


__all__ = ["OutboxRelay"]


async def main() -> None:
    """Run the relay as a standalone process (`python -m ...outbox_relay`)."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    engine = build_engine(settings.sqlalchemy_url)
    broker = build_broker(settings.rabbitmq_url)
    topology = Topology(retry_delays_ms=settings.retry_delays_ms)
    stop = asyncio.Event()
    _install_signal_handlers(stop)

    try:
        try:
            # The relay publishes, so it declares the topology itself: sending to an
            # exchange with no bound queue would drop the message silently.
            await declare_topology_with_retry(
                settings.rabbitmq_url,
                topology,
                attempts=STANDALONE_TOPOLOGY_ATTEMPTS,
                pause_seconds=STANDALONE_TOPOLOGY_PAUSE_SECONDS,
            )
        except Exception:
            logger.exception("broker topology is not ready; the outbox keeps the events")
        await broker.start()
        use_case = PublishOutboxUseCase(
            SqlAlchemyUnitOfWorkFactory(build_session_factory(engine)),
            RabbitEventPublisher(broker, topology),
            SystemClock(),
        )
        logger.info("standalone outbox relay started")
        await OutboxRelay(use_case, settings).run(stop)
    finally:
        await broker.stop()
        await engine.dispose()


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """Turn SIGTERM/SIGINT into a graceful stop."""
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signal_number, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows has no loop signals
            signal.signal(signal_number, lambda *_: stop.set())


if __name__ == "__main__":
    asyncio.run(main())
