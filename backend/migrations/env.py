"""Fresh database migrations. Historical databases are never a rewrite target."""

from alembic import context
from sqlalchemy import create_engine, inspect

from kleos.infrastructure.schema import metadata
from kleos.infrastructure.settings import Settings

config = context.config
target_metadata = metadata()
url = config.get_main_option("sqlalchemy.url") or Settings().database_url


def configure(connection=None):
    options = dict(
        target_metadata=target_metadata,
        version_table="kleos2_alembic_version",
        compare_type=True,
        include_name=lambda name, kind, parents: kind != "table" or name.startswith("kleos2_"),
    )
    if connection is None:
        context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"}, **options)
    else:
        existing = inspect(connection).get_table_names()
        allowed = {
            "checkpoints",
            "checkpoint_blobs",
            "checkpoint_writes",
            "checkpoint_migrations",
            "store",
            "store_migrations",
            "store_vectors",
        }
        if any(not name.startswith("kleos2_") and name not in allowed for name in existing):
            raise RuntimeError("Use a fresh rewrite database; existing application tables are protected")
        context.configure(connection=connection, **options)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    configure()
elif config.attributes.get("connection") is not None:
    configure(config.attributes["connection"])
else:
    engine = create_engine(url)
    try:
        # The legacy-table guard performs introspection and starts a transaction.
        # Own that transaction so Alembic cannot mistake it for an externally
        # managed transaction and silently roll the schema/version row back.
        with engine.begin() as connection:
            configure(connection)
    finally:
        engine.dispose()
