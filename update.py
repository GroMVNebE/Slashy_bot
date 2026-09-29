import asyncpg
import asyncio
from dotenv import load_dotenv
import os
import hashlib

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')


UPDATE_SCHEMA = """
ALTER TABLE user_settings
    ADD COLUMN vc_detailed_stats_enabled BOOLEAN DEFAULT FALSE;
"""


async def update_db():
    db_pool = await asyncpg.create_pool(dsn=DATABASE_URL)

    async with db_pool.acquire() as connection:
        await connection.execute(UPDATE_SCHEMA)
    await db_pool.close()


asyncio.run(update_db())
