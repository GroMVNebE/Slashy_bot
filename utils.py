import os
import logging
import asyncpg
from dotenv import load_dotenv

# Инициализируем логгер для этого модуля
logger = logging.getLogger('slashy.utils')
# Загружаем переменные окружения из .env файла
load_dotenv()


def get_env(key: str) -> str:
    """Получает значение переменной окружения из .env файла. Если переменная не найдена, выбрасывает исключение

    Args:
        key (str): Название переменной окружения (например, 'DISCORD_TOKEN')

    Raises:
        ValueError: Если переменная не найдена в .env файле, выбрасывает исключение с сообщением об ошибке

    Returns:
        str: Значение переменной окружения
    """
    value = os.getenv(key)
    if not value:
        logger.error(f"Переменная {key} не задана в .env")
        raise ValueError(
            f"Переменная {key} не задана в .env")
    return value


DATABASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_levels (
    guild_id BIGINT,
    user_id BIGINT,
    xp BIGINT DEFAULT 0,
    level INTEGER DEFAULT 1,
    PRIMARY KEY (guild_id, user_id)
);
"""


async def setup_database(pool: asyncpg.Pool):
    """Создаёт необходимые таблицы в базе данных

    Args:
        pool (asyncpg.Pool): Пул соединений с базой данных
    """
    try:
        async with pool.acquire() as connection:
            await connection.execute(DATABASE_SCHEMA)
        logger.info("Провека и создание таблиц завершены \\^o^/")
    except Exception as e:
        logger.error(f"Ошибка при настройке базы данных: {e}", exc_info=True)
        raise e


async def add_xp(user_id: int, guild_id: int, xp: int, pool: asyncpg.Pool):
    """Добавляет опыт пользователю

    Args:
        user_id (int): ID пользователя
        guild_id (int): ID сервера
        xp (int): Количество опыта для добавления
        pool (asyncpg.Pool): Пул соединений с базой данных
    """
    try:
        async with pool.acquire() as connection:
            # Начисляем опыт и получаем обновлённые данные о пользователе
            row = await connection.fetchrow("""
                INSERT INTO user_levels (guild_id, user_id, xp)
                VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, user_id) DO UPDATE
                SET xp = user_levels.xp + EXCLUDED.xp
                RETURNING xp, level
            """, guild_id, user_id, xp)
            # Получаем данные о текущем опыте и уровне пользователя
            curr_xp = row['xp']
            curr_level = row['level']
            # Вычисляем требуемый опыт для следующего уровня
            required_xp = 80*curr_level + 20*curr_level**2
            # Поднимаем уровень пользователя, если опыта достаточно
            level_up = False
            while curr_xp >= required_xp:
                # Поднимаем уровень пользователя, отнимая требуемый опыт
                curr_xp -= required_xp
                curr_level += 1
                required_xp = 80*curr_level + 20*curr_level**2
                level_up = True
            # Обновляем уровень и оставшийся опыт в базе данных, если уровень был повышен
            if level_up:
                await connection.execute("""
                    UPDATE user_levels
                    SET level = $1, xp = $2
                    WHERE guild_id = $3 AND user_id = $4
                """, curr_level, curr_xp, guild_id, user_id)
        logger.info(
            f"Начислено {xp} опыт пользователю {user_id} на сервере {guild_id}, текущий уровень: {curr_level}, текущий опыт: {curr_xp}")
    except Exception as e:
        logger.error(f"Ошибка при начислении опыта: {e}", exc_info=True)
        raise e
