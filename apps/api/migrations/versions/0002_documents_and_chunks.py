"""Documents and embedded chunks, with an HNSW index for cosine search.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-27

* ``documents.status`` is VARCHAR + CHECK, not a PostgreSQL ENUM: values can be
  added or removed with a constraint change and downgrades stay trivial.
* ``documents.sha256`` is UNIQUE: content-based deduplication and race-free
  concurrent uploads (``INSERT ... ON CONFLICT DO NOTHING``).
* ``chunks.embedding`` is ``vector(384)`` (multilingual-e5-small). The HNSW index
  uses ``vector_cosine_ops``, matching the ``<=>`` operator used by search; a
  mismatched operator class would make the planner skip the index silently.
* The index is created in the migration transaction (not ``CONCURRENTLY``): the
  table is new and empty. Rebuilding it on a large live table would need
  ``CREATE INDEX CONCURRENTLY`` in a non-transactional migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name=op.f("ck_documents_status_valid"),
        ),
        sa.CheckConstraint("page_count >= 1", name=op.f("ck_documents_page_count_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("sha256", name=op.f("uq_documents_sha256")),
    )
    op.create_index(
        "ix_documents_created_at_id",
        "documents",
        [sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name=op.f("ck_chunks_page_range_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_id_ordinal"),
    )
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_table("chunks")
    op.drop_table("documents")
