"""Production persistence uses PostgreSQL; in-memory savers belong only in tests."""
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.store.postgres.aio import AsyncPostgresStore


@asynccontextmanager
async def postgres_runtime(database_url: str, *, index=None, setup=False):
    serializer = JsonPlusSerializer(allowed_msgpack_modules=[])
    async with AsyncPostgresSaver.from_conn_string(database_url, serde=serializer) as saver:
        async with AsyncPostgresStore.from_conn_string(database_url, index=index) as store:
            if setup:
                await saver.setup()
                await store.setup()
            yield saver, store
