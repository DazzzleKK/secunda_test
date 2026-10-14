"""Output of the payment gateway port."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChargeResult:
    """The gateway answer: a business decision, not a technical status."""

    approved: bool
    reason: str | None = None


__all__ = ["ChargeResult"]
