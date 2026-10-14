"""Status transitions of the payment entity and the outbox event factory."""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import UUID

import pytest

from payments.domain.entities import OutboxEvent
from payments.domain.enums import EventType, PaymentStatus
from payments.domain.exceptions import InvalidMetadataError
from payments.domain.services import MAX_METADATA_BYTES, build_request_fingerprint
from payments.domain.value_objects import PaymentOutcome
from tests.unit.fakes import FIXED_NOW, make_payment

LATER = FIXED_NOW + timedelta(seconds=3)


def test_new_payment_starts_pending_with_zero_counters() -> None:
    payment = make_payment(payment_id=UUID(int=1))

    assert payment.status is PaymentStatus.PENDING
    assert payment.is_final is False
    assert payment.processed_at is None
    assert payment.failure_reason is None
    assert payment.webhook_attempts == 0
    assert payment.webhook_delivered_at is None
    assert payment.webhook_last_error is None


def test_created_payment_derives_its_request_fingerprint() -> None:
    payment = make_payment(amount="100.50", idempotency_key="key-1")

    expected = build_request_fingerprint(
        payment.money, payment.description, payment.metadata, payment.webhook_url
    )

    assert payment.request_fingerprint == expected
    assert payment.idempotency_key == "key-1"


def test_mark_succeeded_moves_the_payment_to_a_final_state() -> None:
    payment = make_payment()

    assert payment.mark_succeeded(LATER) is True
    assert payment.status is PaymentStatus.SUCCEEDED
    assert payment.is_final is True
    assert payment.processed_at == LATER
    assert payment.updated_at == LATER
    assert payment.failure_reason is None


def test_mark_succeeded_is_ignored_when_already_final() -> None:
    payment = make_payment()
    payment.mark_succeeded(LATER)

    assert payment.mark_succeeded(LATER + timedelta(seconds=1)) is False
    assert payment.processed_at == LATER


def test_mark_failed_stores_the_reason() -> None:
    payment = make_payment()

    assert payment.mark_failed("insufficient funds", LATER) is True
    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "insufficient funds"
    assert payment.processed_at == LATER


def test_mark_failed_is_ignored_after_success() -> None:
    payment = make_payment()
    payment.mark_succeeded(LATER)

    assert payment.mark_failed("too late", LATER) is False
    assert payment.status is PaymentStatus.SUCCEEDED
    assert payment.failure_reason is None


def test_apply_outcome_with_succeeded_leaves_no_failure_reason() -> None:
    payment = make_payment()
    payment.failure_reason = "stale reason"

    assert payment.apply_outcome(PaymentOutcome(status=PaymentStatus.SUCCEEDED), now=LATER) is True
    assert payment.status is PaymentStatus.SUCCEEDED
    assert payment.failure_reason is None


def test_apply_outcome_with_failed_keeps_the_reason() -> None:
    payment = make_payment()

    outcome = PaymentOutcome(status=PaymentStatus.FAILED, reason="card declined")
    assert payment.apply_outcome(outcome, now=LATER) is True
    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "card declined"


def test_apply_outcome_is_idempotent_for_a_final_payment() -> None:
    payment = make_payment()
    payment.apply_outcome(PaymentOutcome(status=PaymentStatus.SUCCEEDED), now=LATER)

    late = PaymentOutcome(status=PaymentStatus.FAILED, reason="late duplicate")
    assert payment.apply_outcome(late, now=LATER) is False
    assert payment.status is PaymentStatus.SUCCEEDED
    assert payment.failure_reason is None


def test_webhook_pending_is_false_while_the_payment_is_pending() -> None:
    assert make_payment().webhook_pending is False


def test_webhook_pending_is_true_for_a_final_payment_without_delivery() -> None:
    payment = make_payment()
    payment.mark_succeeded(LATER)

    assert payment.webhook_pending is True


def test_webhook_pending_is_false_after_delivery() -> None:
    payment = make_payment()
    payment.mark_succeeded(LATER)
    payment.webhook_delivered_at = LATER

    assert payment.webhook_pending is False


def test_payment_created_event_points_to_the_new_payments_queue() -> None:
    payment = make_payment(payment_id=UUID(int=7), amount="100.50")

    event = OutboxEvent.payment_created(payment, now=FIXED_NOW, event_id=UUID(int=99))

    assert event.id == UUID(int=99)
    assert event.aggregate_id == payment.id
    assert event.event_type is EventType.PAYMENT_CREATED
    assert event.routing_key == "payments.new"
    assert event.created_at == FIXED_NOW
    assert event.attempts == 0
    assert event.last_error is None
    assert event.published_at is None


def test_payment_created_event_payload_describes_the_payment() -> None:
    payment = make_payment(payment_id=UUID(int=7), amount="100.50")

    event = OutboxEvent.payment_created(payment, now=FIXED_NOW, event_id=UUID(int=99))

    assert event.payload == {
        "event_id": str(UUID(int=99)),
        "event": "payment.created",
        "payment_id": str(payment.id),
        "attempt": 0,
        "status": "pending",
        "amount": "100.50",
        "currency": "RUB",
        "occurred_at": FIXED_NOW.isoformat(),
    }


def test_payment_created_event_payload_survives_json_round_trip() -> None:
    event: OutboxEvent = OutboxEvent.payment_created(make_payment(), now=FIXED_NOW)

    restored = json.loads(json.dumps(event.payload))

    assert restored["event"] == "payment.created"
    assert restored["status"] == "pending"


def test_mutating_the_source_metadata_does_not_change_the_stored_copy() -> None:
    source = {"order": {"items": [1, 2]}}

    payment = make_payment(metadata=source)
    fingerprint = payment.request_fingerprint

    source["order"]["items"].append(3)
    source["order"]["items"][0] = 999

    assert payment.metadata == {"order": {"items": [1, 2]}}
    assert payment.request_fingerprint == fingerprint


def test_metadata_order_does_not_change_the_stored_representation() -> None:
    first = make_payment(metadata={"b": 2, "a": 1})
    second = make_payment(metadata={"a": 1, "b": 2})

    assert first.metadata == second.metadata
    assert first.request_fingerprint == second.request_fingerprint


def test_metadata_over_the_size_limit_is_rejected() -> None:
    oversized = {"blob": "x" * (MAX_METADATA_BYTES + 1)}

    with pytest.raises(InvalidMetadataError) as exc_info:
        make_payment(metadata=oversized)

    assert exc_info.value.code == "invalid_metadata"
    assert exc_info.value.details["limit_bytes"] == MAX_METADATA_BYTES


def test_metadata_must_be_json_serialisable() -> None:
    with pytest.raises(InvalidMetadataError):
        make_payment(metadata={"when": FIXED_NOW})
