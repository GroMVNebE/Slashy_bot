import asyncpg
import asyncio
from dotenv import load_dotenv
import os
import hashlib

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')


UPDATE_SCHEMA = """
ALTER TABLE voice_detailed_sessions
    ALTER COLUMN guild_id TYPE TEXT,
    ALTER COLUMN user_id TYPE TEXT;

ALTER TABLE voice_detailed_sessions
    ALTER COLUMN session_id TYPE BIGINT;

ALTER SEQUENCE voice_detailed_sessions_session_id_seq AS bigint MAXVALUE 9223372036854775807;
"""


async def update_db():
    db_pool = await asyncpg.create_pool(dsn=DATABASE_URL)

    async with db_pool.acquire() as connection:
        # Обновляем типы guild_id и user_id на TEXT, чтобы хранить хэш-код значений
        # А также расширяем диапазон значений session_id, изменяя его тип с SERIAL на BIGSERIAL (BIGINT)
        await connection.execute(UPDATE_SCHEMA)
        # Изменяем хранящиеся в таблице guild_id и user_id на их хэш-код
        data = await connection.fetch(
            "SELECT session_id, guild_id, user_id FROM voice_detailed_sessions;")
        updated_data = []
        for row in data:
            # Получаем данные из БД
            session_id = row['session_id']
            guild_id, user_id = str(row['guild_id']), str(row['user_id'])

            # Хэшируем значения в sha256
            gid_hash_code = hashlib.sha256(
                guild_id.encode('utf-8')).hexdigest()
            uid_hash_code = hashlib.sha256(user_id.encode('utf-8')).hexdigest()

            # Сохраняем обновлённые данные
            updated_data.append((gid_hash_code, uid_hash_code, session_id))

        # Обновляем данные в БД
        if updated_data:
            await connection.executemany(
                """
                UPDATE voice_detailed_sessions 
                SET guild_id = $1, user_id = $2 
                WHERE session_id = $3
                """,
                updated_data
            )

    await db_pool.close()


asyncio.run(update_db())
