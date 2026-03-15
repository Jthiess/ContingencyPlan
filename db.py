import logging
import os

import asyncpg

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_SCHEMA, DB_USER

logger = logging.getLogger(__name__)


class Database:
    """Thin async wrapper around an asyncpg connection pool."""

    def __init__(self):
        self.pool: asyncpg.Pool | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def connect(self):
        logger.info("Connecting to PostgreSQL %s@%s:%s/%s (schema: %s)", DB_USER, DB_HOST, DB_PORT, DB_NAME, DB_SCHEMA)
        self.pool = await asyncpg.create_pool(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            min_size=2,
            max_size=10,
            server_settings={"search_path": DB_SCHEMA},
        )
        logger.info("Database connection pool established")

    async def close(self):
        if self.pool:
            await self.pool.close()
            logger.info("Database connection pool closed")

    # ── query helpers ────────────────────────────────────────────────────────

    async def execute(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def executemany(self, query: str, args_list: list):
        async with self.pool.acquire() as conn:
            return await conn.executemany(query, args_list)

    async def fetchrow(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetch(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchval(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    # ── schema management ────────────────────────────────────────────────────

    async def init_schema(self):
        """Create the schema (if needed) and run DatabaseSetup.sql if tables don't exist yet."""
        async with self.pool.acquire() as conn:
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}")

        exists = await self.fetchval(
            "SELECT EXISTS("
            "  SELECT 1 FROM information_schema.tables"
            "  WHERE table_schema = $1 AND table_name = 'guilds'"
            ")",
            DB_SCHEMA,
        )
        if exists:
            logger.info("Database schema already exists — skipping init")
            return

        schema_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "DatabaseSetup.sql"
        )
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_sql = f.read()

        async with self.pool.acquire() as conn:
            await conn.execute(schema_sql)

        logger.info("Database schema created from DatabaseSetup.sql")
