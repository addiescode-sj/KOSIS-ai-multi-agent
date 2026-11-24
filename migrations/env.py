"""Alembic environment: DATABASE_URL (kosis_store.db.database_url) and the kosis_store models."""

from alembic import context

from kosis_store.db import database_url, make_engine
from kosis_store.models import Base
import kosis_store.log_models  # noqa: E402,F401  (T3-48: the log tables are on the same Base)
import kosis_store.service_models  # noqa: E402,F401  (T3-51: sessions, requests, feedback)

config = context.config
target_metadata = Base.metadata

# T3-51: LangGraph's PostgresSaver.setup() creates and migrates these itself; autogenerate must not drop them
LANGGRAPH_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (type_ == "table" and reflected and compare_to is None and name in LANGGRAPH_TABLES)


def run_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url") or database_url(),
                      target_metadata=target_metadata, literal_binds=True, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = config.attributes.get("connection")  # tests pass their own connection
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = make_engine(config.get_main_option("sqlalchemy.url") or None)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
