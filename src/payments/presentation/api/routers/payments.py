"""Payment endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Depends, Header, Response, status

from payments.application.dto.create_payment_command import CreatePaymentCommand
from payments.application.use_cases.create_payment import CreatePaymentUseCase
from payments.application.use_cases.get_payment import GetPaymentUseCase
from payments.presentation.api.errors import ERROR_RESPONSES
from payments.presentation.api.exceptions import MissingIdempotencyKeyError
from payments.presentation.api.schemas import (
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentResponse,
)
from payments.presentation.api.security import require_api_key

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
PAYMENTS_PREFIX = "/api/v1/payments"

router = APIRouter(
    prefix=PAYMENTS_PREFIX,
    tags=["payments"],
    route_class=DishkaRoute,
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PaymentCreateResponse,
    summary="Create a payment",
    description=(
        "Stores the payment and its outbox event in one transaction and answers "
        "immediately. The same Idempotency-Key with the same body returns the "
        "stored payment; the same key with a different body is a conflict."
    ),
    responses=ERROR_RESPONSES,
)
async def create_payment(
    request: PaymentCreateRequest,
    response: Response,
    use_case: FromDishka[CreatePaymentUseCase],
    idempotency_key: Annotated[str | None, Header(alias=IDEMPOTENCY_KEY_HEADER)] = None,
) -> PaymentCreateResponse:
    """Accept a payment for asynchronous processing."""
    if idempotency_key is None:
        raise MissingIdempotencyKeyError()

    result = await use_case.execute(
        CreatePaymentCommand(
            amount=Decimal(request.amount),
            currency=request.currency,
            description=request.description,
            metadata=request.metadata,
            webhook_url=request.webhook_url,
            idempotency_key=idempotency_key,
        )
    )

    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    response.headers["Location"] = f"{PAYMENTS_PREFIX}/{result.payment.payment_id}"
    return PaymentCreateResponse.from_dto(result.payment)


@router.get(
    "/{payment_id}",
    response_model=PaymentResponse,
    summary="Get a payment",
    responses=ERROR_RESPONSES,
)
async def get_payment(
    payment_id: UUID,
    use_case: FromDishka[GetPaymentUseCase],
) -> PaymentResponse:
    """Return the full state of a payment, including its webhook result."""
    payment = await use_case.execute(payment_id)
    return PaymentResponse.from_dto(payment)


__all__ = ["router"]
