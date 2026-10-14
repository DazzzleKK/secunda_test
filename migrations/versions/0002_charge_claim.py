"""Add the charge claim column.

Revision ID: 0002_charge_claim
Revises: 0001_initial
Create Date: 2026-01-02

`charge_started_at` makes the call to the payment gateway exclusive: one
conditional UPDATE claims the payment before the money moves, so two copies of the
same event cannot both be charged. The claim is a lease, see the use case.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_charge_claim"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("charge_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payments", "charge_started_at")
