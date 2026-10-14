"""The outbox relay against a real broker."""

from __future__ import annotations

import asyncio

import pytest

from payments.application.use_cases.publish_outbox import PublishOutboxUseCase
from payments.infrastructure.messaging.broker import build_broker
from payments.infrastructure.messaging.outbox_relay import OutboxRelay
from payments.infrastructure.messaging.publisher import RabbitEventPublisher
from payments.infrastructure.messaging.topology import (
    WORK_QUEUE_NAME,
    Topology,
    declare_topology,
)
from payments.infrastructure.system import SystemClock
from tests.integration.helpers import (
    RabbitMq,
    build_outbox_event,
    build_payment,
    queue_messages,
    wait_for_queue_messages,
)

pytestmark = pytest.mark.integration

# The same ladder as in the other broker tests: RabbitMQ refuses to redeclare a
# queue with different arguments, and the container is shared by the session.
RETRY_DELAYS_MS = (300, 900)


async def _seed_event(uow_factory) -> None:
    payment = build_payment(idempotency_key="relay-1")
    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)
        await uow.outbox.add(build_outbox_event(payment))
        await uow.commit()


async def test_a_batch_is_published_and_stamped(uow_factory, rabbitmq: RabbitMq) -> None:
    topology = Topology(retry_delays_ms=RETRY_DELAYS_MS)
    await declare_topology(rabbitmq.amqp_url, topology)
    await _seed_event(uow_factory)
    before = queue_messages(rabbitmq.management_url, WORK_QUEUE_NAME)

    broker = build_broker(rabbitmq.amqp_url)
    await broker.start()
    try:
        use_case = PublishOutboxUseCase(
            uow_factory, RabbitEventPublisher(broker, topology), SystemClock()
        )
        published = await use_case.execute(batch_size=10)
    finally:
        await broker.stop()

    assert published == 1

    async with uow_factory() as uow:
        assert await uow.outbox.fetch_unpublished(10) == []

    depth = await wait_for_queue_messages(
        rabbitmq.management_url, WORK_QUEUE_NAME, minimum=before + 1
    )
    assert depth >= before + 1


async def test_the_loop_stops_on_request_and_publishes_what_it_finds(
    uow_factory, rabbitmq: RabbitMq, settings_factory
) -> None:
    topology = Topology(retry_delays_ms=RETRY_DELAYS_MS)
    await declare_topology(rabbitmq.amqp_url, topology)
    await _seed_event(uow_factory)
    before = queue_messages(rabbitmq.management_url, WORK_QUEUE_NAME)

    broker = build_broker(rabbitmq.amqp_url)
    await broker.start()
    stop = asyncio.Event()
    try:
        relay = OutboxRelay(
            PublishOutboxUseCase(
                uow_factory, RabbitEventPublisher(broker, topology), SystemClock()
            ),
            settings_factory(outbox_poll_interval_ms=50),
        )
        task = asyncio.create_task(relay.run(stop))
        depth = await wait_for_queue_messages(
            rabbitmq.management_url, WORK_QUEUE_NAME, minimum=before + 1
        )
        stop.set()
        await asyncio.wait_for(task, timeout=5.0)
    finally:
        await broker.stop()

    assert depth >= before + 1
    assert task.done()
