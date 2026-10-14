"""Dishka providers: the composition root of the API process.

The ports are registered under their protocol types, so use cases never see a
concrete adapter. APP scope holds the settings and the connection pool, REQUEST
scope builds the use cases per request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from payments.application.ports.clock import ClockProtocol
from payments.application.ports.id_generator import IdGeneratorProtocol
from payments.application.ports.unit_of_work import UnitOfWorkFactoryProtocol
from payments.application.use_cases.create_payment import CreatePaymentUseCase
from payments.application.use_cases.get_payment import GetPaymentUseCase
from payments.infrastructure.config import Settings
from payments.infrastructure.db.session import build_engine, build_session_factory
from payments.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWorkFactory
from payments.infrastructure.system import SystemClock, UuidGenerator


class AppProvider(Provider):
    """Wires configuration, database access, clock and use cases."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

    @provide(scope=Scope.APP)
    def settings(self) -> Settings:
        """The settings the process was started with."""
        return self._settings

    @provide(scope=Scope.APP)
    async def engine(self, settings: Settings) -> AsyncIterator[AsyncEngine]:
        """One connection pool per process, disposed on shutdown."""
        engine = build_engine(settings.sqlalchemy_url)
        yield engine
        await engine.dispose()

    @provide(scope=Scope.APP)
    def session_factory(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        """Factory of database sessions."""
        return build_session_factory(engine)

    @provide(scope=Scope.APP)
    def unit_of_work_factory(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> UnitOfWorkFactoryProtocol:
        """Factory of transactions."""
        return SqlAlchemyUnitOfWorkFactory(session_factory)

    @provide(scope=Scope.APP)
    def clock(self) -> ClockProtocol:
        """Wall clock of the service."""
        return SystemClock()

    @provide(scope=Scope.APP)
    def id_generator(self) -> IdGeneratorProtocol:
        """Identifier generator of the service."""
        return UuidGenerator()

    @provide(scope=Scope.REQUEST)
    def create_payment_use_case(
        self,
        unit_of_work_factory: UnitOfWorkFactoryProtocol,
        clock: ClockProtocol,
        id_generator: IdGeneratorProtocol,
    ) -> CreatePaymentUseCase:
        """Use case behind POST /api/v1/payments."""
        return CreatePaymentUseCase(unit_of_work_factory, clock, id_generator)

    @provide(scope=Scope.REQUEST)
    def get_payment_use_case(
        self,
        unit_of_work_factory: UnitOfWorkFactoryProtocol,
    ) -> GetPaymentUseCase:
        """Use case behind GET /api/v1/payments/{payment_id}."""
        return GetPaymentUseCase(unit_of_work_factory)


__all__ = ["AppProvider"]
