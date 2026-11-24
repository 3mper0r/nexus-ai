"""Enable the pgvector extension.

Revision ID: 0001
Revises:
Create Date: 2026-11-24

The extension is managed here (not in a Docker init script) so every environment
that runs migrations (local, CI, production) gets it identically and its
presence is versioned alongside the schema.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
