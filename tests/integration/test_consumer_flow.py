"""End-to-end flow: a queued event becomes a processed payment and a webhook.

The consumer, the broker and a local webhook receiver run for real here; only the
payment gateway is the emulator, and it is configured to be deterministic.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from payments.application.use_cases.deliver_webhook import DeliverWebhookUseCase
from payments.application.use_cases.process_payment import ProcessPaymentUseCase
from payments.application.use_cases.publish_outbox import PublishOutboxUseCase
from payments.domain.entities import Payment
from payments.infrastructure.db.session import build_engine, build_session_factory
from payments.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
from payments.infrastructure.gateway.fake_gateway import FakePaymentGateway
from payments.infrastructure.messaging.broker import build_broker
from payments.infrastructure.messaging.consumer import PaymentConsumer
from payments.infrastructure.messaging.publisher import RabbitEventPublisher
from payments.infrastructure.messaging.topology import (
    ATTEMPT_HEADER,
    DLQ_NAME,
    ERROR_HEADER,
    WORK_QUEUE_NAME,
    WORK_ROUTING_KEY,
    Topology,
    declare_topology,
)
from payments.infrastructure.system import SystemClock
from payments.infrastructure.webhook.http_sender import HttpWebhookSender
from payments.infrastructure.webhook.signer import (
    ATTEMPT_HEADER as WEBHOOK_ATTEMPT_HEADER,
)
from payments.infrastructure.webhook.signer import (
    SIGNATURE_HEADER,
    verify_signature,
)
from tests.integration.helpers import (
    FIXED_NOW,
    WEBHOOK_SECRET,
    RabbitMq,
    build_outbox_event,
    build_payment,
    fetch_queue_messages,
    queue_messages,
    wait_for_queue_messages,
)

pytestmark = pytest.mark.integration

# The same ladder as in the other broker tests: RabbitMQ refuses to redeclare a
# queue with different arguments, and the container is shared by the session.
RETRY_DELAYS_MS = (300, 900)
PROCESSING_TIMEOUT_SECONDS = 25.0
DUPLICATE_SETTLE_SECONDS = 1.5


@dataclass
class WebhookSink:
    """Local receiver that records what the consumer delivered."""

    url: str
    events: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, bool] = field(default_factory=lambda: {"fail": False})

    def fail(self, *, enabled: bool) -> None:
        """Switch the receiver into "always 500" mode."""
        self.state["fail"] = enabled


@pytest_asyncio.fixture
async def webhook_sink() -> AsyncIterator[WebhookSink]:
    """Run a real HTTP receiver on a free port for the duration of a test."""
    app = FastAPI()
    sink = WebhookSink(url="")

    @app.post("/hook")
    async def hook(request: Request) -> JSONResponse:
        body = await request.body()
        sink.events.append(
            {
                "signature_valid": verify_signature(
                    WEBHOOK_SECRET, body, request.headers.get(SIGNATURE_HEADER, "")
                ),
                "attempt": request.headers.get(WEBHOOK_ATTEMPT_HEADER),
                "payload": json.loads(body),
            }
        )
        if sink.state["fail"]:
            return JSONResponse({"error": "receiver is failing"}, status_code=500)
        return JSONResponse({"accepted": True})

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        sink.url = f"http://127.0.0.1:{port}/hook"
        yield sink
    finally:
        server.should_exit = True
        await task


@dataclass
class ConsumerHarness:
    """Everything a test needs to drive one payment through the pipeline."""

    uow_factory: SqlAlchemyUnitOfWorkFactory
    broker: Any
    topology: Topology

    async def submit(self, *, webhook_url: str, idempotency_key: str) -> UUID:
        """Store a payment with its outbox event and publish it."""
        payment = build_payment(
            idempotency_key=idempotency_key,
            webhook_url=webhook_url,
            now=FIXED_NOW,
        )
        async with self.uow_factory() as uow:
            await uow.payments.add_if_absent(payment)
            await uow.outbox.add(build_outbox_event(payment, now=FIXED_NOW))
            await uow.commit()
        published = await PublishOutboxUseCase(
            self.uow_factory,
            RabbitEventPublisher(self.broker, self.topology),
            SystemClock(),
        ).execute(batch_size=10)
        assert published == 1
        return payment.id

    async def republish(self, payment_id: UUID) -> None:
        """Publish the same event again, as a redelivery would."""
        await self.broker.publish(
            {
                "event_id": str(UUID(int=1)),
                "event": "payment.created",
                "payment_id": str(payment_id),
                "attempt": 0,
            },
            exchange=self.topology.exchange,
            routing_key=WORK_ROUTING_KEY,
            headers={ATTEMPT_HEADER: 0},
            persist=True,
        )

    async def load(self, payment_id: UUID) -> Payment | None:
        """Read the stored payment through a fresh transaction."""
        async with self.uow_factory() as uow:
            return await uow.payments.get(payment_id)

    async def wait_for_status(
        self, payment_id: UUID, status: str, *, timeout: float = PROCESSING_TIMEOUT_SECONDS
    ) -> Payment | None:
        """Wait until the payment reaches the expected status."""
        deadline = asyncio.get_running_loop().time() + timeout
        payment: Payment | None = None
        while asyncio.get_running_loop().time() < deadline:
            payment = await self.load(payment_id)
            if payment is not None and payment.status.value == status:
                return payment
            await asyncio.sleep(0.1)
        return payment


@pytest_asyncio.fixture
async def consumer(
    postgres_url: str,
    rabbitmq: RabbitMq,
) -> AsyncIterator[ConsumerHarness]:
    """Run the real consumer against the test database and broker."""
    topology = Topology(retry_delays_ms=RETRY_DELAYS_MS)
    engine = build_engine(postgres_url)
    uow_factory = SqlAlchemyUnitOfWorkFactory(build_session_factory(engine))
    http_client = httpx.AsyncClient(trust_env=False)
    broker = build_broker(rabbitmq.amqp_url)
    payment_consumer = PaymentConsumer(
        broker=broker,
        topology=topology,
        process_payment=ProcessPaymentUseCase(
            uow_factory,
            FakePaymentGateway(success_rate=1.0, min_delay_ms=0, max_delay_ms=0),
            SystemClock(),
        ),
        deliver_webhook=DeliverWebhookUseCase(
            uow_factory,
            HttpWebhookSender(http_client, secret=WEBHOOK_SECRET, timeout_seconds=2.0),
            SystemClock(),
        ),
    )
    payment_consumer.register(broker)
    await declare_topology(rabbitmq.amqp_url, topology)
    await broker.start()
    try:
        yield ConsumerHarness(uow_factory=uow_factory, broker=broker, topology=topology)
    finally:
        await broker.stop()
        await http_client.aclose()
        await engine.dispose()


async def wait_for_events(
    sink: WebhookSink, minimum: int, *, timeout: float = PROCESSING_TIMEOUT_SECONDS
) -> int:
    """Wait until the receiver got at least `minimum` requests."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if len(sink.events) >= minimum:
            return len(sink.events)
        await asyncio.sleep(0.1)
    return len(sink.events)


def _payload_of(message: dict[str, Any]) -> dict[str, Any]:
    """Decode the body of a message taken from the management API."""
    raw = message.get("payload")
    if isinstance(raw, str):
        return dict(json.loads(raw))
    return dict(raw)


async def test_a_payment_is_processed_and_the_webhook_is_signed(
    consumer: ConsumerHarness,
    webhook_sink: WebhookSink,
) -> None:
    payment_id = await consumer.submit(webhook_url=webhook_sink.url, idempotency_key="flow-success")

    delivered = await wait_for_events(webhook_sink, 1)

    assert delivered == 1
    event = webhook_sink.events[0]
    assert event["signature_valid"] is True
    assert event["payload"]["payment_id"] == str(payment_id)
    assert event["payload"]["status"] == "succeeded"
    assert event["payload"]["amount"] == "100.50"
    assert event["attempt"] == "1"

    payment = await consumer.load(payment_id)
    assert payment is not None
    assert payment.status.value == "succeeded"
    assert payment.processed_at is not None
    assert payment.webhook_attempts == 1
    assert payment.webhook_delivered_at is not None
    assert payment.webhook_last_error is None


async def test_a_failing_webhook_lands_in_the_dead_letter_queue(
    consumer: ConsumerHarness,
    webhook_sink: WebhookSink,
    rabbitmq: RabbitMq,
) -> None:
    baseline = queue_messages(rabbitmq.management_url, DLQ_NAME)
    webhook_sink.fail(enabled=True)

    payment_id = await consumer.submit(webhook_url=webhook_sink.url, idempotency_key="flow-dlq")
    depth = await wait_for_queue_messages(rabbitmq.management_url, DLQ_NAME, minimum=baseline + 1)

    assert depth >= baseline + 1
    assert await wait_for_events(webhook_sink, 3) == 3

    messages = fetch_queue_messages(rabbitmq.management_url, DLQ_NAME)
    message = next(item for item in messages if _payload_of(item)["payment_id"] == str(payment_id))
    headers = message["properties"]["headers"]
    assert headers[ATTEMPT_HEADER] == 3
    assert "webhook_delivery_failed" in headers[ERROR_HEADER]

    payment = await consumer.load(payment_id)
    assert payment is not None
    assert payment.status.value == "succeeded"
    assert payment.webhook_delivered_at is None
    assert payment.webhook_last_error is not None


async def test_a_redelivered_event_does_not_charge_or_notify_twice(
    consumer: ConsumerHarness,
    webhook_sink: WebhookSink,
) -> None:
    payment_id = await consumer.submit(
        webhook_url=webhook_sink.url, idempotency_key="flow-duplicate"
    )
    assert await wait_for_events(webhook_sink, 1) == 1
    delivered = await consumer.load(payment_id)
    assert delivered is not None

    await consumer.republish(payment_id)
    await asyncio.sleep(DUPLICATE_SETTLE_SECONDS)

    assert len(webhook_sink.events) == 1
    payment = await consumer.load(payment_id)
    assert payment is not None
    assert payment.status.value == "succeeded"
    assert payment.webhook_attempts == 1


async def test_the_working_queue_is_drained_by_the_consumer(
    consumer: ConsumerHarness,
    webhook_sink: WebhookSink,
    rabbitmq: RabbitMq,
) -> None:
    payment_id = await consumer.submit(webhook_url=webhook_sink.url, idempotency_key="flow-queue")
    await wait_for_events(webhook_sink, 1)

    assert await consumer.wait_for_status(payment_id, "succeeded") is not None
    assert queue_messages(rabbitmq.management_url, WORK_QUEUE_NAME) == 0


__all__: list[str] = []
