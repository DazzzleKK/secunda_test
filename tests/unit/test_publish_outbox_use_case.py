"""Publish outbox use case (relay step) on fake ports."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

from payments.application.use_cases.publish_outbox import PublishOutboxUseCase
from tests.unit.fakes import (
    FIXED_NOW,
    FakeClock,
    FakeEventPublisher,
    FakeStore,
    FakeUnitOfWorkFactory,
    make_outbox_event,
    seed_outbox,
)


async def test_publish_outbox_publishes_pending_events_oldest_first_and_marks_them(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    first = seed_outbox(store, make_outbox_event(event_id=UUID(int=1), now=FIXED_NOW))
    second = seed_outbox(
        store,
        make_outbox_event(event_id=UUID(int=2), now=FIXED_NOW + timedelta(seconds=1)),
    )
    publisher = FakeEventPublisher()
    use_case = PublishOutboxUseCase(uow_factory, publisher, clock)

    published = await use_case.execute()

    assert published == 2
    assert publisher.published == [first.id, second.id]
    assert store.outbox[first.id].published_at == clock.now()
    assert store.outbox[second.id].published_at == clock.now()
    assert uow_factory.commit_count == 1


async def test_publish_outbox_skips_events_that_were_already_published(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    seed_outbox(
        store,
        replace(make_outbox_event(event_id=UUID(int=1)), published_at=FIXED_NOW),
    )
    pending = seed_outbox(store, make_outbox_event(event_id=UUID(int=2)))
    publisher = FakeEventPublisher()
    use_case = PublishOutboxUseCase(uow_factory, publisher, clock)

    published = await use_case.execute()

    assert published == 1
    assert publisher.published == [pending.id]


async def test_publish_outbox_records_a_transient_failure_and_stops_the_batch(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    healthy = seed_outbox(store, make_outbox_event(event_id=UUID(int=1), now=FIXED_NOW))
    unreachable = seed_outbox(
        store,
        make_outbox_event(event_id=UUID(int=2), now=FIXED_NOW + timedelta(seconds=1)),
    )
    publisher = FakeEventPublisher(failing_event_ids=[unreachable.id])
    use_case = PublishOutboxUseCase(uow_factory, publisher, clock)

    published = await use_case.execute()

    assert published == 1
    assert publisher.published == [healthy.id]
    assert store.outbox[unreachable.id].published_at is None
    assert "broker is unreachable" in (store.outbox[unreachable.id].last_error or "")
    assert store.outbox[healthy.id].published_at == clock.now()


async def test_a_transient_failure_does_not_consume_the_publication_attempts(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    failing = seed_outbox(store, make_outbox_event(event_id=UUID(int=1), now=FIXED_NOW))
    publisher = FakeEventPublisher(failing_event_ids=[failing.id])
    use_case = PublishOutboxUseCase(uow_factory, publisher, clock)

    for _ in range(5):
        await use_case.execute()

    assert store.outbox[failing.id].attempts == 0
    assert store.outbox[failing.id].last_error is not None


async def test_an_unpublishable_event_consumes_an_attempt(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    broken = seed_outbox(store, make_outbox_event(event_id=UUID(int=1), now=FIXED_NOW))
    publisher = FakeEventPublisher(unpublishable_event_ids=[broken.id])
    use_case = PublishOutboxUseCase(uow_factory, publisher, clock)

    published = await use_case.execute()

    assert published == 0
    assert store.outbox[broken.id].attempts == 1
    assert "payload is not serialisable" in (store.outbox[broken.id].last_error or "")


async def test_publish_outbox_returns_zero_when_nothing_is_pending(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    publisher = FakeEventPublisher()
    use_case = PublishOutboxUseCase(uow_factory, publisher, clock)

    published = await use_case.execute()

    assert published == 0
    assert publisher.published == []
    assert uow_factory.commit_count == 0


async def test_publish_outbox_honours_the_batch_size(
    store: FakeStore,
    clock: FakeClock,
    uow_factory: FakeUnitOfWorkFactory,
) -> None:
    for index in range(3):
        seed_outbox(
            store,
            make_outbox_event(
                event_id=UUID(int=index + 1),
                now=FIXED_NOW + timedelta(seconds=index),
            ),
        )
    publisher = FakeEventPublisher()
    use_case = PublishOutboxUseCase(uow_factory, publisher, clock)

    published = await use_case.execute(batch_size=2)

    assert published == 2
    assert len(publisher.published) == 2
    still_pending = [event for event in store.outbox.values() if event.published_at is None]
    assert len(still_pending) == 1
