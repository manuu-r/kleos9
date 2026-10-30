from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from kleos.config import get_settings
from kleos.db import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
url = get_settings().database_url
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        postgres = connection.dialect.name == "postgresql"
        if postgres:
            acquired = connection.scalar(text("SELECT pg_try_advisory_lock(84922420)"))
            connection.commit()  # Session lock survives; Alembic owns its own DDL transaction.
            if not acquired:
                raise RuntimeError("Another migration is running; reconcile its outcome before retrying")
        try:
            context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if postgres:
                if connection.in_transaction():
                    connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(84922420)"))
                connection.commit()
