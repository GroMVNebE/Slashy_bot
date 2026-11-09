import asyncpg
import asyncio
from dotenv import load_dotenv
import os

from utils import get_required_xp

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')


UPDATE_SCHEMA = """
ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS anon_stats_enabled BOOLEAN DEFAULT TRUE;
"""


async def update_db():
    db_pool = await asyncpg.create_pool(dsn=DATABASE_URL)

    async with db_pool.acquire() as connection:
        await connection.execute(UPDATE_SCHEMA)
    await db_pool.close()


if __name__ == '__main__':
    asyncio.run(update_db())
