"""Alembic environment of the operator app: only the `admin` schema, version table in it too (T3-57)."""

from alembic import context
from sqlalchemy import create_engine, text

from admin.app.config import database_url
from admin.app.models import SCHEMA, AdminBase

config = context.config


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return getattr(obj, "schema", SCHEMA) == SCHEMA if type_ == "table" else True


def _ensure_schema(conn) -> None:
    # In the server the schema is made by admin/sql/grants.sql and owned by kosis_admin (which may not CREATE in the
    # database), so only make it when it is missing (local runs and tests).
    if conn.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :s"), {"s": SCHEMA}).first() is None:
        conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    conn.commit()  # end the check's transaction, or alembic would run inside it and never commit


def _run(conn) -> None:
    _ensure_schema(conn)
    context.configure(connection=conn, target_metadata=AdminBase.metadata, version_table_schema=SCHEMA,
                      include_schemas=True, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")  # tests pass their own connection
if connection is not None:
    _run(connection)
else:
    engine = create_engine(config.get_main_option("sqlalchemy.url") or database_url())
    with engine.connect() as conn:
        _run(conn)
