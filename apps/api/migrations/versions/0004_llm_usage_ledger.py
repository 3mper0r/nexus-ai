"""LLM usage ledger: spend that survives conversation deletion (M2 audit).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-28

The daily budget used to be summed over ``messages``, so deleting a conversation
deleted its spend and reopened the budget. ``llm_usage`` has one row per admitted
generation, keyed by the assistant message id but with **no foreign key**: deleting
conversations or messages never removes spend. Existing reservations are backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("reserved_cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_usage")),
    )
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])
    op.execute(
        "INSERT INTO llm_usage (id, model, reserved_cost_usd, cost_usd, created_at) "
        "SELECT id, COALESCE(model, ''), reserved_cost_usd, cost_usd, created_at "
        "FROM messages WHERE role = 'assistant' AND reserved_cost_usd IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_table("llm_usage")
