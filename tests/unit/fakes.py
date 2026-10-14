"""In-memory fakes of the application ports, plus small test builders.

The fakes keep the semantics of the real adapters that matter for the use cases:

* reads return deep copies (a real adapter maps rows to fresh entities);
* `add_if_absent` behaves like the unique index on `idempotency_key`;
* leaving a unit of work without `commit()` rolls the transaction back;
* terminal transitions are guarded, so a duplicate delivery cannot overwrite a
  final status.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from typing import Any
from uuid import UUID

from payments.application.dto.charge_result import ChargeResult
from payments.application.dto.create_payment_command import CreatePaymentCommand
from payments.application.dto.webhook_delivery_result import WebhookDeliveryResult
from payments.application.dto.webhook_payload import WebhookPayload
from payments.application.ports.clock import ClockProtocol
from payments.application.ports.event_publisher import EventPublisherProtocol
from payments.application.ports.id_generator import IdGeneratorProtocol
from payments.application.ports.outbox_repository import OutboxRepositoryProtocol
from payments.application.ports.payment_gateway import PaymentGatewayProtocol
from payments.application.ports.payment_repository import PaymentRepositoryProtocol
from payments.application.ports.unit_of_work import (
    UnitOfWorkFactoryProtocol,
    UnitOfWorkProtocol,
)
from payments.application.ports.webhook_sender import WebhookSenderProtocol
from payments.domain.entities import OutboxEvent, Payment
from payments.domain.enums import Currency
from payments.domain.exceptions import (
    EventPayloadInvalidError,
    PaymentGatewayUnavailableError,
)
from payments.domain.value_objects import IdempotencyKey, Money, PaymentOutcome, WebhookTarget

FIXED_NOW = datetime(2024, 5, 1, 12, 0, tzinfo=UTC)
DEFAULT_AMOUNT = "100.50"
DEFAULT_DESCRIPTION = "Payment for order 42"
DEFAULT_METADATA: Mapping[str, Any] = {"order_id": 42}
DEFAULT_IDEMPOTENCY_KEY = "key-0001"
DEFAULT_WEBHOOK_URL = "https://merchant.example.com/hooks/payments"

_TEST_IDS = count(1)


def next_test_id() -> UUID:
    """Return a deterministic identifier for test fixtures."""
    return UUID(int=next(_TEST_IDS))


def make_payment(
    *,
    payment_id: UUID | None = None,
    amount: str = DEFAULT_AMOUNT,
    currency: Currency = Currency.RUB,
    description: str = DEFAULT_DESCRIPTION,
    metadata: Mapping[str, Any] | None = None,
    idempotency_key: str = DEFAULT_IDEMPOTENCY_KEY,
    webhook_url: str = DEFAULT_WEBHOOK_URL,
    now: datetime = FIXED_NOW,
) -> Payment:
    """Build a pending payment through the domain factory."""
    return Payment.create(
        payment_id=payment_id if payment_id is not None else next_test_id(),
        money=Money(amount=Decimal(amount), currency=currency),
        description=description,
        metadata=DEFAULT_METADATA if metadata is None else metadata,
        idempotency_key=IdempotencyKey(idempotency_key),
        webhook_target=WebhookTarget(webhook_url),
        now=now,
    )


def make_command(
    *,
    amount: str = DEFAULT_AMOUNT,
    currency: Currency = Currency.RUB,
    description: str = DEFAULT_DESCRIPTION,
    metadata: Mapping[str, Any] | None = None,
    webhook_url: str = DEFAULT_WEBHOOK_URL,
    idempotency_key: str = DEFAULT_IDEMPOTENCY_KEY,
) -> CreatePaymentCommand:
    """Build a create payment command with the same defaults as `make_payment`."""
    return CreatePaymentCommand(
        amount=Decimal(amount),
        currency=currency,
        description=description,
        metadata=DEFAULT_METADATA if metadata is None else metadata,
        webhook_url=webhook_url,
        idempotency_key=idempotency_key,
    )


def make_outbox_event(
    *,
    event_id: UUID | None = None,
    aggregate_id: UUID | None = None,
    now: datetime = FIXED_NOW,
) -> OutboxEvent:
    """Build a `payment.created` outbox event for a synthetic payment."""
    payment = make_payment(
        payment_id=aggregate_id if aggregate_id is not None else next_test_id(),
        now=now,
    )
    return OutboxEvent.payment_created(
        payment,
        now=now,
        event_id=event_id if event_id is not None else next_test_id(),
    )


@dataclass
class FakeStore:
    """The in-memory state shared by every fake unit of work (stands for the database)."""

    payments: dict[UUID, Payment] = field(default_factory=dict)
    outbox: dict[UUID, OutboxEvent] = field(default_factory=dict)
    claims: dict[UUID, datetime] = field(default_factory=dict)


def seed_payment(store: FakeStore, payment: Payment) -> Payment:
    """Put a payment straight into the fake database (test setup only)."""
    store.payments[payment.id] = copy.deepcopy(payment)
    return payment


def seed_outbox(store: FakeStore, event: OutboxEvent) -> OutboxEvent:
    """Put an outbox row straight into the fake database (test setup only)."""
    store.outbox[event.id] = copy.deepcopy(event)
    return event


@dataclass
class ReadBlindness:
    """Harness knob: how many upcoming idempotency reads should miss the stored row.

    It emulates the race the unique index is there for: a competing request commits
    the row between our fast-path read and our insert.
    """

    remaining: int = 0

    def consume(self) -> bool:
        """Return True when the next idempotency read must report 'not found'."""
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


class FakeClock(ClockProtocol):
    """A deterministic clock."""

    def __init__(self, start: datetime = FIXED_NOW) -> None:
        self._now = start

    def now(self) -> datetime:
        """Return the frozen current moment."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self._now += timedelta(seconds=seconds)


class FakeIdGenerator(IdGeneratorProtocol):
    """A deterministic identifier generator."""

    def __init__(self, start: int = 0) -> None:
        self._counter = start

    def new_id(self) -> UUID:
        """Return the next identifier in sequence."""
        self._counter += 1
        return UUID(int=self._counter)


class FakePaymentRepository(PaymentRepositoryProtocol):
    """In-memory payment storage."""

    def __init__(
        self,
        store: FakeStore,
        clock: ClockProtocol,
        *,
        blindness: ReadBlindness | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._blindness = blindness

    async def add_if_absent(self, payment: Payment) -> bool:
        """Insert the payment unless its idempotency key is already taken."""
        for stored in self._store.payments.values():
            if stored.idempotency_key == payment.idempotency_key:
                return False
        self._store.payments[payment.id] = copy.deepcopy(payment)
        return True

    async def get(self, payment_id: UUID) -> Payment | None:
        """Return a copy of the stored payment, or None."""
        stored = self._store.payments.get(payment_id)
        return None if stored is None else copy.deepcopy(stored)

    async def get_by_idempotency_key(self, key: str) -> Payment | None:
        """Return a copy of the payment stored under the key, or None."""
        if self._blindness is not None and self._blindness.consume():
            return None
        for stored in self._store.payments.values():
            if stored.idempotency_key == key:
                return copy.deepcopy(stored)
        return None

    async def apply_outcome(self, payment_id: UUID, outcome: PaymentOutcome) -> bool:
        """Apply the outcome to the stored payment with the guarded transition."""
        stored = self._store.payments.get(payment_id)
        if stored is None:
            return False
        return stored.apply_outcome(outcome, now=self._clock.now())

    async def claim_for_charging(
        self,
        payment_id: UUID,
        *,
        now: datetime,
        lease: timedelta,
    ) -> bool:
        """Claim the payment unless it is final or the lease is still held."""
        stored = self._store.payments.get(payment_id)
        if stored is None or stored.is_final:
            return False
        claimed_at = self._store.claims.get(payment_id)
        if claimed_at is not None and claimed_at > now - lease:
            return False
        self._store.claims[payment_id] = now
        return True

    async def register_webhook_attempt(
        self,
        payment_id: UUID,
        result: WebhookDeliveryResult,
        *,
        now: datetime,
    ) -> None:
        """Record the attempt and its outcome on the stored payment."""
        stored = self._store.payments.get(payment_id)
        if stored is None:
            return
        stored.webhook_attempts += 1
        stored.updated_at = now
        if result.delivered:
            stored.webhook_delivered_at = now
            stored.webhook_last_error = None
        else:
            stored.webhook_last_error = result.error


class FakeOutboxRepository(OutboxRepositoryProtocol):
    """In-memory outbox storage."""

    def __init__(self, store: FakeStore, clock: ClockProtocol) -> None:
        self._store = store
        self._clock = clock

    async def add(self, event: OutboxEvent) -> None:
        """Append the event to the fake outbox."""
        self._store.outbox[event.id] = copy.deepcopy(event)

    async def fetch_unpublished(self, limit: int) -> Sequence[OutboxEvent]:
        """Return unpublished events, oldest first, limited to `limit`."""
        pending = [
            copy.deepcopy(event)
            for event in self._store.outbox.values()
            if event.published_at is None
        ]
        pending.sort(key=lambda event: event.created_at)
        return pending[:limit]

    async def mark_published(self, event_id: UUID, *, now: datetime) -> None:
        """Stamp `published_at` on the stored event."""
        stored = self._store.outbox.get(event_id)
        if stored is not None:
            self._store.outbox[event_id] = replace(stored, published_at=now)

    async def register_failure(
        self,
        event_id: UUID,
        error: str,
        *,
        permanent: bool = False,
    ) -> None:
        """Store the error; only a permanent failure bumps the attempts."""
        stored = self._store.outbox.get(event_id)
        if stored is None:
            return
        attempts = stored.attempts + 1 if permanent else stored.attempts
        self._store.outbox[event_id] = replace(stored, attempts=attempts, last_error=error)

    async def oldest_unpublished_age(self) -> timedelta | None:
        """Return the age of the oldest unpublished event, or None."""
        ages = [
            self._clock.now() - event.created_at
            for event in self._store.outbox.values()
            if event.published_at is None
        ]
        return min(ages) if ages else None


class FakeUnitOfWork(UnitOfWorkProtocol):
    """A transaction scope over the fake database.

    The state is snapshotted on enter and restored unless the scope was committed,
    which makes a missing `commit()` visible in tests.
    """

    payments: PaymentRepositoryProtocol
    outbox: OutboxRepositoryProtocol

    def __init__(
        self,
        store: FakeStore,
        clock: ClockProtocol,
        *,
        blindness: ReadBlindness | None = None,
        on_commit: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self._on_commit = on_commit
        self._committed = False
        self._snapshot: tuple[dict[UUID, Payment], dict[UUID, OutboxEvent]] | None = None
        self.payments = FakePaymentRepository(store, clock, blindness=blindness)
        self.outbox = FakeOutboxRepository(store, clock)

    async def __aenter__(self) -> FakeUnitOfWork:
        """Snapshot the fake database and return the scope."""
        self._snapshot = (
            copy.deepcopy(self._store.payments),
            copy.deepcopy(self._store.outbox),
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Roll back the scope unless it was committed."""
        if not self._committed:
            self._restore()
        self._snapshot = None

    async def commit(self) -> None:
        """Keep every change made inside the scope."""
        self._committed = True
        if self._on_commit is not None:
            self._on_commit()

    async def rollback(self) -> None:
        """Discard every change made inside the scope."""
        self._restore()

    def _restore(self) -> None:
        if self._snapshot is not None:
            self._store.payments, self._store.outbox = self._snapshot
            self._snapshot = None


class FakeUnitOfWorkFactory(UnitOfWorkFactoryProtocol):
    """Creates a fresh transaction scope per call over one fake database."""

    def __init__(
        self,
        store: FakeStore,
        clock: ClockProtocol,
        *,
        blindness: ReadBlindness | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._blindness = blindness
        self.created = 0
        self.commit_count = 0

    def __call__(self) -> FakeUnitOfWork:
        """Return a new unit of work and count it."""
        self.created += 1
        return FakeUnitOfWork(
            self._store,
            self._clock,
            blindness=self._blindness,
            on_commit=self._count_commit,
        )

    def _count_commit(self) -> None:
        self.commit_count += 1


class FakePaymentGateway(PaymentGatewayProtocol):
    """A gateway double with a scripted decision."""

    def __init__(
        self,
        *,
        approved: bool = True,
        reason: str | None = None,
        unavailable: bool = False,
    ) -> None:
        self._approved = approved
        self._reason = reason
        self._unavailable = unavailable
        self.calls: list[tuple[UUID, UUID]] = []

    async def charge(self, payment: Payment, *, idempotency_key: UUID) -> ChargeResult:
        """Record the call and return the scripted decision."""
        self.calls.append((payment.id, idempotency_key))
        if self._unavailable:
            raise PaymentGatewayUnavailableError(payment.id, "connection refused")
        return ChargeResult(approved=self._approved, reason=self._reason)


class FakeEventPublisher(EventPublisherProtocol):
    """A publisher double that can fail events in two different ways."""

    def __init__(
        self,
        *,
        failing_event_ids: Sequence[UUID] = (),
        unpublishable_event_ids: Sequence[UUID] = (),
    ) -> None:
        self._failing = set(failing_event_ids)
        self._unpublishable = set(unpublishable_event_ids)
        self.published: list[UUID] = []

    async def publish(self, event: OutboxEvent) -> None:
        """Record the event or fail as configured."""
        if event.id in self._unpublishable:
            raise EventPayloadInvalidError(event.id, reason="payload is not serialisable")
        if event.id in self._failing:
            raise RuntimeError(f"broker is unreachable for event {event.id}")
        self.published.append(event.id)


class FakeWebhookSender(WebhookSenderProtocol):
    """A webhook sender double with a scripted result."""

    def __init__(
        self,
        *,
        delivered: bool = True,
        status_code: int | None = 200,
        error: str | None = None,
    ) -> None:
        self._delivered = delivered
        self._status_code = status_code
        self._error = error
        self.calls: list[tuple[WebhookTarget, WebhookPayload, int]] = []

    async def send(
        self,
        target: WebhookTarget,
        payload: WebhookPayload,
        *,
        attempt: int,
    ) -> WebhookDeliveryResult:
        """Record the call and return the scripted result."""
        self.calls.append((target, payload, attempt))
        return WebhookDeliveryResult(
            delivered=self._delivered,
            status_code=self._status_code,
            error=self._error,
        )
