"""The topology the service declares on a real broker."""

from __future__ import annotations

import pytest
from faststream.rabbit import ExchangeType

from payments.infrastructure.messaging.topology import (
    DLQ_NAME,
    DLX_EXCHANGE_NAME,
    EXCHANGE_NAME,
    RETRY_EXCHANGE_NAME,
    WORK_QUEUE_NAME,
    WORK_ROUTING_KEY,
    Topology,
    declare_topology,
)
from tests.integration.helpers import (
    RabbitMq,
    exchange_document,
    queue_bindings,
    queue_document,
)

pytestmark = pytest.mark.integration

RETRY_DELAYS_MS = (300, 900)
RABBITMQ_TOPIC_TYPE = "topic"
RABBITMQ_DIRECT_TYPE = "direct"


async def test_declaring_the_topology_twice_is_idempotent(rabbitmq: RabbitMq) -> None:
    topology = Topology(retry_delays_ms=RETRY_DELAYS_MS)

    await declare_topology(rabbitmq.amqp_url, topology)
    await declare_topology(rabbitmq.amqp_url, topology)


async def test_the_exchanges_have_the_expected_types(rabbitmq: RabbitMq) -> None:
    topology = Topology(retry_delays_ms=RETRY_DELAYS_MS)
    await declare_topology(rabbitmq.amqp_url, topology)

    work_exchange = exchange_document(rabbitmq.management_url, EXCHANGE_NAME)
    retry_exchange = exchange_document(rabbitmq.management_url, RETRY_EXCHANGE_NAME)
    dlx_exchange = exchange_document(rabbitmq.management_url, DLX_EXCHANGE_NAME)

    assert work_exchange["type"] == RABBITMQ_TOPIC_TYPE
    assert work_exchange["durable"] is True
    assert retry_exchange["type"] == RABBITMQ_DIRECT_TYPE
    assert dlx_exchange["type"] == RABBITMQ_DIRECT_TYPE


async def test_the_working_queue_is_durable_and_bound_to_the_exchange(
    rabbitmq: RabbitMq,
) -> None:
    topology = Topology(retry_delays_ms=RETRY_DELAYS_MS)
    await declare_topology(rabbitmq.amqp_url, topology)

    document = queue_document(rabbitmq.management_url, WORK_QUEUE_NAME)
    bindings = queue_bindings(rabbitmq.management_url, WORK_QUEUE_NAME)

    assert document["durable"] is True
    assert document["auto_delete"] is False
    assert (EXCHANGE_NAME, WORK_ROUTING_KEY) in bindings


async def test_every_retry_tier_is_durable_with_its_ttl_and_dead_letter(
    rabbitmq: RabbitMq,
) -> None:
    topology = Topology(retry_delays_ms=RETRY_DELAYS_MS)
    await declare_topology(rabbitmq.amqp_url, topology)

    for target in topology.retry_tiers:
        document = queue_document(rabbitmq.management_url, target.queue_name)
        arguments = document["arguments"]
        bindings = queue_bindings(rabbitmq.management_url, target.queue_name)

        assert document["durable"] is True, target.queue_name
        assert arguments["x-message-ttl"] == target.delay_ms, target.queue_name
        assert arguments["x-dead-letter-exchange"] == EXCHANGE_NAME
        assert arguments["x-dead-letter-routing-key"] == WORK_ROUTING_KEY
        assert (RETRY_EXCHANGE_NAME, target.routing_key) in bindings


async def test_the_dead_letter_queue_is_durable_and_has_no_consumer(
    rabbitmq: RabbitMq,
) -> None:
    topology = Topology(retry_delays_ms=RETRY_DELAYS_MS)
    await declare_topology(rabbitmq.amqp_url, topology)

    document = queue_document(rabbitmq.management_url, DLQ_NAME)

    assert document["durable"] is True
    assert document["consumer_details"] == []


def test_the_service_uses_a_direct_exchange_for_the_dead_letter_queue() -> None:
    topology = Topology(retry_delays_ms=RETRY_DELAYS_MS)

    assert topology.exchange.type is ExchangeType.TOPIC
    assert topology.retry_exchange.type is ExchangeType.DIRECT
    assert topology.dlx_exchange.type is ExchangeType.DIRECT
