"""Ports of the application layer: the contracts infrastructure implements."""

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

__all__ = [
    "ClockProtocol",
    "EventPublisherProtocol",
    "IdGeneratorProtocol",
    "OutboxRepositoryProtocol",
    "PaymentGatewayProtocol",
    "PaymentRepositoryProtocol",
    "UnitOfWorkFactoryProtocol",
    "UnitOfWorkProtocol",
    "WebhookSenderProtocol",
]
