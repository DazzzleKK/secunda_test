"""Shared fixtures for the unit tests."""

from __future__ import annotations

import pytest

from tests.unit.fakes import (
    FIXED_NOW,
    FakeClock,
    FakeIdGenerator,
    FakeStore,
    FakeUnitOfWorkFactory,
)


@pytest.fixture
def clock() -> FakeClock:
    """A clock frozen at a fixed UTC moment."""
    return FakeClock(FIXED_NOW)


@pytest.fixture
def ids() -> FakeIdGenerator:
    """A deterministic identifier generator."""
    return FakeIdGenerator()


@pytest.fixture
def store() -> FakeStore:
    """An empty in-memory database."""
    return FakeStore()


@pytest.fixture
def uow_factory(store: FakeStore, clock: FakeClock) -> FakeUnitOfWorkFactory:
    """A factory opening new transactions over the in-memory database."""
    return FakeUnitOfWorkFactory(store, clock)
