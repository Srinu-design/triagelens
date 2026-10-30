from contextlib import asynccontextmanager

from pgvector.asyncpg import register_vector
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings


class _AsyncpgHalfvec(HALFVEC):
    cache_ok = True

    def bind_processor(self, dialect):
        # DECISION: pgvector 0.5.1's binary codec needs native vectors, not SQLAlchemy text.
        return None


def create_engine(settings: Settings, *, null_pool: bool = False) -> AsyncEngine:
    options = {"poolclass": NullPool} if null_pool else {"pool_size": 5, "max_overflow": 5}
    engine = create_async_engine(settings.database_url, **options)
    engine.sync_engine.dialect.colspecs = {
        **engine.sync_engine.dialect.colspecs,
        HALFVEC: _AsyncpgHalfvec,
    }

    @event.listens_for(engine.sync_engine, "connect")
    def _register(dbapi_connection, connection_record):
        dbapi_connection.run_async(register_vector)

    return engine


@asynccontextmanager
async def read_transaction(engine: AsyncEngine):
    # asyncpg opens it with BEGIN READ ONLY: one round trip instead of BEGIN + SET TRANSACTION
    async with engine.connect() as connection:
        await connection.execution_options(postgresql_readonly=True)
        async with connection.begin():
            yield connection


@asynccontextmanager
async def write_transaction(engine: AsyncEngine):
    async with engine.begin() as connection:
        yield connection


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


async def pgvector_version(engine: AsyncEngine) -> str | None:
    async with read_transaction(engine) as connection:
        return await connection.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )


def pgvector_supported(version: str | None) -> bool:
    return version is not None and version_tuple(version) >= (0, 8, 0)
