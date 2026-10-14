"""Create payment use case."""

from __future__ import annotations

from payments.application.dto.create_payment_command import CreatePaymentCommand
from payments.application.dto.create_payment_result import CreatePaymentResult
from payments.application.dto.payment_dto import PaymentDto
from payments.application.ports.clock import ClockProtocol
from payments.application.ports.id_generator import IdGeneratorProtocol
from payments.application.ports.unit_of_work import UnitOfWorkFactoryProtocol
from payments.domain.entities import OutboxEvent, Payment
from payments.domain.exceptions import IdempotencyConflictError, PaymentNotVisibleError
from payments.domain.value_objects import IdempotencyKey, Money, WebhookTarget


class CreatePaymentUseCase:
    """Create a payment together with its outbox event, exactly once per key.

    The flow is:

    1. validate the request through value objects;
    2. read the idempotency key inside the transaction; a stored payment is
       resolved as a replay (same fingerprint) or as a conflict (different one);
    3. `add_if_absent` writes the new row and returns False when the unique index
       rejected it;
    4. on success the outbox event is appended and **one** `commit()` covers both
       inserts, which is what makes the dual write impossible;
    5. when the insert was rejected, the transaction is dropped, a **new** unit of
       work re-reads the key and resolves the winner of the race.

    The read-then-insert is only a fast path: correctness comes from the unique
    index on `idempotency_key` plus `INSERT ... ON CONFLICT DO NOTHING` in the
    repository adapter, not from the `SELECT`.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactoryProtocol,
        clock: ClockProtocol,
        ids: IdGeneratorProtocol,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids

    async def execute(self, command: CreatePaymentCommand) -> CreatePaymentResult:
        """Create the payment or replay the stored result of the same key.

        Raises:
            InvalidAmountError: the amount is not a positive two-digit decimal.
            PaymentsDomainError: the idempotency key or the webhook target is invalid.
            IdempotencyConflictError: the key is already used by another payload.
            PaymentNotVisibleError: the conflicting row is not readable yet.
        """
        money = Money(amount=command.amount, currency=command.currency)
        key = IdempotencyKey(command.idempotency_key)
        target = WebhookTarget(command.webhook_url)
        now = self._clock.now()
        payment = Payment.create(
            payment_id=self._ids.new_id(),
            money=money,
            description=command.description,
            metadata=command.metadata,
            idempotency_key=key,
            webhook_target=target,
            now=now,
        )
        fingerprint = payment.request_fingerprint

        inserted = False
        async with self._uow_factory() as uow:
            existing = await uow.payments.get_by_idempotency_key(key.value)
            if existing is not None:
                return self._resolve(existing, fingerprint)
            inserted = await uow.payments.add_if_absent(payment)
            if inserted:
                await uow.outbox.add(
                    OutboxEvent.payment_created(payment, now=now, event_id=self._ids.new_id())
                )
                await uow.commit()

        if inserted:
            return CreatePaymentResult(payment=PaymentDto.from_entity(payment), replayed=False)

        # The insert lost the race against a concurrent request with the same key.
        async with self._uow_factory() as uow:
            winner = await uow.payments.get_by_idempotency_key(key.value)
        if winner is None:
            raise PaymentNotVisibleError(payment.id)
        return self._resolve(winner, fingerprint)

    @staticmethod
    def _resolve(payment: Payment, fingerprint: str) -> CreatePaymentResult:
        """Turn a stored payment into a replay or raise a conflict."""
        if payment.request_fingerprint != fingerprint:
            raise IdempotencyConflictError(payment.idempotency_key)
        return CreatePaymentResult(payment=PaymentDto.from_entity(payment), replayed=True)


__all__ = ["CreatePaymentUseCase"]
