"""Publish outbox use case (the relay step)."""

from __future__ import annotations

from payments.application.ports.clock import ClockProtocol
from payments.application.ports.event_publisher import EventPublisherProtocol
from payments.application.ports.unit_of_work import UnitOfWorkFactoryProtocol

DEFAULT_BATCH_SIZE = 100


class PublishOutboxUseCase:
    """Publish a batch of outbox events and mark them as published.

    One transaction covers the whole batch: the adapter locks the rows with
    `FOR UPDATE SKIP LOCKED`, every event is published and `published_at` is only
    stamped after the broker confirmed the message. A failing event does not stop
    the batch — it records the error and is retried on the next tick.
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
                except Exception as exc:  # a single bad event must not stop the batch
                    await uow.outbox.register_failure(event.id, str(exc))
                    continue
                await uow.outbox.mark_published(event.id, now=self._clock.now())
                published += 1
            await uow.commit()
        return published


__all__ = ["DEFAULT_BATCH_SIZE", "PublishOutboxUseCase"]
