"""Integration tests of the transactional boundary."""

from __future__ import annotations

import pytest

from payments.infrastructure.db.models import PaymentModel
from tests.integration.helpers import build_payment, count_rows

pytestmark = pytest.mark.integration


async def test_commit_persists_the_changes(uow_factory, session) -> None:
    payment = build_payment()

    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)
        await uow.commit()

    assert await count_rows(session, PaymentModel) == 1


async def test_changes_are_dropped_without_commit(uow_factory, session) -> None:
    payment = build_payment()

    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)

    assert await count_rows(session, PaymentModel) == 0


async def test_exception_inside_the_scope_rolls_back(uow_factory, session) -> None:
    payment = build_payment()

    with pytest.raises(RuntimeError, match="boom"):
        async with uow_factory() as uow:
            await uow.payments.add_if_absent(payment)
            raise RuntimeError("boom")

    assert await count_rows(session, PaymentModel) == 0


async def test_each_call_opens_an_independent_transaction(uow_factory, session) -> None:
    committed = build_payment(idempotency_key="committed")
    dropped = build_payment(idempotency_key="dropped")

    async with uow_factory() as first:
        await first.payments.add_if_absent(committed)
        await first.commit()

    async with uow_factory() as second:
        await second.payments.add_if_absent(dropped)

    assert await count_rows(session, PaymentModel) == 1


async def test_session_is_released_after_the_scope(uow_factory) -> None:
    async with uow_factory() as uow:
        pass

    with pytest.raises(RuntimeError, match="outside of its context manager"):
        _ = uow.session


async def test_rollback_can_be_called_explicitly(uow_factory, session) -> None:
    payment = build_payment()

    async with uow_factory() as uow:
        await uow.payments.add_if_absent(payment)
        await uow.rollback()

    assert await count_rows(session, PaymentModel) == 0
