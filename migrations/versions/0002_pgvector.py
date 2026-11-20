"""pgvector: the vector store tables (T3-47, KOS-138; phase 2 T2-44)

The same definition phase 2's kosis_rag.adapters.vectorstore creates when it finds no tables (SCHEMA there), so a
database made by this migration and one made by a phase 2 build alone hold the same tables. One row per table,
region chunk or hypothetical question; `key` is the table key the search filters on. No approximate (HNSW) index:
the collections are a few thousand rows and the search is an exact cosine scan.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    if "vector_collection" in sa.inspect(op.get_bind()).get_table_names():
        # A phase 2 build or the Chroma migration script ran on this database first and made the same tables
        # (kosis_rag.adapters.vectorstore.SCHEMA): keep them and their rows.
        return
    op.create_table(
        "vector_collection",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "vector_item",
        sa.Column("collection", sa.Text(), sa.ForeignKey("vector_collection.name", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=True),
        sa.Column("document", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
    )
    op.create_index("vector_item_key", "vector_item", ["collection", "key"])


def downgrade() -> None:
    op.drop_index("vector_item_key", table_name="vector_item")
    op.drop_table("vector_item")
    op.drop_table("vector_collection")
    op.execute("DROP EXTENSION IF EXISTS vector")
