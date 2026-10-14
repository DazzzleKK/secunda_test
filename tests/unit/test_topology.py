"""The retry ladder and the broker objects of the topology."""

from __future__ import annotations

import pytest
from faststream.rabbit import ExchangeType

from payments.infrastructure.messaging.topology import (
    DLQ_NAME,
    EXCHANGE_NAME,
    RETRY_EXCHANGE_NAME,
    WORK_QUEUE_NAME,
    Topology,
)


def test_the_ladder_has_one_tier_per_configured_delay() -> None:
    topology = Topology(retry_delays_ms=(5_000, 25_000))

    assert topology.max_attempts == 3
    assert [tier.queue_name for tier in topology.retry_tiers] == [
        "payments.retry.1",
        "payments.retry.2",
    ]
    assert [tier.delay_ms for tier in topology.retry_tiers] == [5_000, 25_000]
    assert [tier.routing_key for tier in topology.retry_tiers] == ["retry.1", "retry.2"]


def test_a_single_delay_means_two_attempts() -> None:
    topology = Topology(retry_delays_ms=(1_000,))

    assert topology.max_attempts == 2
    assert len(topology.retry_tiers) == 1


@pytest.mark.parametrize(
    ("attempt", "expected_tier"),
    [(0, 1), (1, 2)],
)
def test_a_failed_attempt_moves_to_the_next_tier(attempt: int, expected_tier: int) -> None:
    topology = Topology(retry_delays_ms=(5_000, 25_000))

    target = topology.retry_target(attempt)

    assert target is not None
    assert target.tier == expected_tier


def test_the_last_attempt_goes_to_the_dead_letter_queue() -> None:
    topology = Topology(retry_delays_ms=(5_000, 25_000))

    assert topology.retry_target(topology.max_attempts - 1) is None


def test_the_first_attempt_of_a_long_ladder_goes_to_the_first_tier() -> None:
    topology = Topology(retry_delays_ms=(1_000, 2_000, 4_000))

    target = topology.retry_target(0)

    assert topology.max_attempts == 4
    assert target is not None
    assert target.queue_name == "payments.retry.1"


@pytest.mark.parametrize(
    "delays",
    [(), (0,), (-1, 5_000)],
)
def test_invalid_delays_are_rejected(delays: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        Topology(retry_delays_ms=delays)


def test_invalid_prefetch_is_rejected() -> None:
    with pytest.raises(ValueError):
        Topology(retry_delays_ms=(1_000,), prefetch_count=0)


def test_broker_objects_are_durable_and_correctly_typed() -> None:
    topology = Topology(retry_delays_ms=(5_000, 25_000))

    assert topology.exchange.name == EXCHANGE_NAME
    assert topology.exchange.type is ExchangeType.TOPIC
    assert topology.exchange.durable is True

    assert topology.retry_exchange.name == RETRY_EXCHANGE_NAME
    assert topology.retry_exchange.type is ExchangeType.DIRECT

    assert topology.dlx_exchange.type is ExchangeType.DIRECT

    assert topology.work_queue.name == WORK_QUEUE_NAME
    assert topology.work_queue.durable is True

    assert DLQ_NAME == "payments.dlq"
    assert topology.channel.prefetch_count == 10
