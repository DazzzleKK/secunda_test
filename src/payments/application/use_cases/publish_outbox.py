"""Publish outbox use case (the relay step)."""

from __future__ import annotations

import logging

from payments.application.ports.clock import ClockProtocol
from payments.application.ports.event_publisher import EventPublisherProtocol
from payments.application.ports.unit_of_work import UnitOfWorkFactoryProtocol
from payments.domain.exceptions import EventPayloadInvalidError

DEFAULT_BATCH_SIZE = 100
MAX_LOGGED_ERROR_LENGTH = 200

logger = logging.getLogger(__name__)


class PublishOutboxUseCase:
    """Publish a batch of outbox events and mark them as published.

    One transaction covers the whole batch: the adapter locks the rows with
    `FOR UPDATE SKIP LOCKED`, every event is published and `published_at` is only
    stamped after the broker confirmed the message.

    Only a *permanent* failure (a payload that can never be published) moves the
    event towards parking and lets the batch continue. A transient failure means the
    broker is unreachable, so the rest of the batch is left for the next tick —
    trying the remaining events would only stack timeouts while holding the rows.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactoryProtocol,
        publisher: EventPublisherProtocol,
        clock: ClockProtocol,
    ) -> None:
        self._uow_factory = uow_factory
        self._publisher = publisher
        self._clock = clock

    async def execute(self, *, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
        """Publish up to `batch_size` pending events; return how many were published."""
        published = 0
        async with self._uow_factory() as uow:
            events = await uow.outbox.fetch_unpublished(batch_size)
            if not events:
                return 0
            for event in events:
                try:
                    await self._publisher.publish(event)
                except EventPayloadInvalidError as exc:
                    logger.error(
                        "outbox event is not publishable",
                        extra={"event_id": str(event.id), "error": exc.message},
                    )
                    await uow.outbox.register_failure(event.id, exc.message, permanent=True)
                    continue
                except Exception as exc:  # a single bad event must not stop the batch
                    logger.warning(
                        "broker is unreachable, the batch stops here",
                        extra={
                            "event_id": str(event.id),
                            "error": _describe(exc),
                        },
                    )
                    await uow.outbox.register_failure(event.id, _describe(exc), permanent=False)
                    # The broker is down, not the event: the rest of the batch would
                    # wait for the same timeout, so it is left for the next tick.
                    break
                await uow.outbox.mark_published(event.id, now=self._clock.now())
                published += 1
            await uow.commit()
        return published


def _describe(exc: BaseException) -> str:
    """Short, safe description of a publication failure."""
    return f"{type(exc).__name__}: {exc}"[:MAX_LOGGED_ERROR_LENGTH]


__all__ = ["DEFAULT_BATCH_SIZE", "PublishOutboxUseCase"]
