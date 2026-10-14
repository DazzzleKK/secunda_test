"""Integration tests of the outbox repository: locking, ordering and parking."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from payments.infrastructure.db.repositories import (
    DEFAULT_MAX_PUBLISH_ATTEMPTS,
    SqlAlchemyOutboxRepository,
)
from tests.integration.helpers import FIXED_NOW, build_outbox_event, build_payment

pytestmark = pytest.mark.integration

FETCH_TIMEOUT_SECONDS = 5.0


async def test_fetch_unpublished_returns_the_oldest_events_first(uow_factory) -> None:
    events = [
        build_outbox_event(
            build_payment(idempotency_key=f"key-{index}"),
            now=FIXED_NOW + timedelta(seconds=index),
        )
        for index in range(3)
    ]

    async with uow_factory() as uow:
        for event in events:
            await uow.outbox.add(event)
        await uow.commit()

    async with uow_factory() as uow:
        fetched = await uow.outbox.fetch_unpublished(2)

    assert [event.id for event in fetched] == [events[0].id, events[1].id]


async def test_fetch_unpublished_skips_rows_locked_by_another_transaction(
    uow_factory,
) -> None:
    events = [
        build_outbox_event(
            build_payment(idempotency_key=f"locked-{index}"),
            now=FIXED_NOW + timedelta(seconds=index),
        )
        for index in range(3)
    ]

    async with uow_factory() as uow:
        for event in events:
            await uow.outbox.add(event)
        await uow.commit()

    async with uow_factory() as first, uow_factory() as second:
        locked_by_first = await asyncio.wait_for(
            first.outbox.fetch_unpublished(2), timeout=FETCH_TIMEOUT_SECONDS
        )
        locked_by_second = await asyncio.wait_for(
            second.outbox.fetch_unpublished(2), timeout=FETCH_TIMEOUT_SECONDS
        )

    first_ids = {event.id for event in locked_by_first}
    second_ids = {event.id for event in locked_by_second}

    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == {event.id for event in events}


async def test_mark_published_hides_the_event_from_the_next_fetch(uow_factory) -> None:
    first_event = build_outbox_event(build_payment(idempotency_key="published"))
    second_event = build_outbox_event(
        build_payment(idempotency_key="still-pending"), now=FIXED_NOW + timedelta(seconds=1)
    )

    async with uow_factory() as uow:
        await uow.outbox.add(first_event)
        await uow.outbox.add(second_event)
        await uow.commit()

    async with uow_factory() as uow:
        events = await uow.outbox.fetch_unpublished(10)
        assert len(events) == 2
        await uow.outbox.mark_published(first_event.id, now=FIXED_NOW)
        await uow.commit()

    async with uow_factory() as uow:
        remaining = await uow.outbox.fetch_unpublished(10)

    assert [event.id for event in remaining] == [second_event.id]
    assert remaining[0].attempts == 0


async def test_mark_published_counts_the_publication_attempt(uow_factory, session) -> None:
    event = build_outbox_event(build_payment())

    async with uow_factory() as uow:
        await uow.outbox.add(event)
        await uow.commit()

    async with uow_factory() as uow:
        await uow.outbox.mark_published(event.id, now=FIXED_NOW)
        await uow.commit()

    repository = SqlAlchemyOutboxRepository(session)
    assert await repository.fetch_unpublished(10) == []


async def test_register_failure_keeps_the_event_retryable(uow_factory) -> None:
    event = build_outbox_event(build_payment())

    async with uow_factory() as uow:
        await uow.outbox.add(event)
        await uow.commit()

    async with uow_factory() as uow:
        await uow.outbox.register_failure(event.id, "broker is down")
        await uow.commit()

    async with uow_factory() as uow:
        fetched = await uow.outbox.fetch_unpublished(10)

    assert [item.id for item in fetched] == [event.id]
    assert fetched[0].attempts == 1
    assert fetched[0].last_error == "broker is down"


async def test_events_are_parked_after_too_many_failed_publications(uow_factory) -> None:
    event = build_outbox_event(build_payment())

    async with uow_factory() as uow:
        await uow.outbox.add(event)
        await uow.commit()

    for _ in range(DEFAULT_MAX_PUBLISH_ATTEMPTS):
        async with uow_factory() as uow:
            await uow.outbox.register_failure(event.id, "broker is down")
            await uow.commit()

    async with uow_factory() as uow:
        assert await uow.outbox.fetch_unpublished(10) == []
        age = await uow.outbox.oldest_unpublished_age()

    assert age is not None


async def test_oldest_unpublished_age_is_none_when_the_outbox_is_empty(uow_factory) -> None:
    async with uow_factory() as uow:
        assert await uow.outbox.oldest_unpublished_age() is None
