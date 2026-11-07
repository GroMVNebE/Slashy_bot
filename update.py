import asyncpg
import asyncio
from dotenv import load_dotenv
import os

from utils import get_required_xp

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')


UPDATE_SCHEMA = """
CREATE INDEX IF NOT EXISTS start_time_index ON voice_detailed_sessions (start_time);
"""

LEVELS_BACKUP_TABLE = 'user_levels_backup_exp_1688'
"""### Резервная копия user_levels до пересчёта уровней
Её наличие означает, что пересчёт уже выполнен - повторный запуск пересчитал бы уровни ещё раз"""


def get_old_required_xp(level: int) -> int:
    """### Функция для вычисления требуемого опыта по старой формуле (степень 1.688)
    Копия прежней версии :func:`utils.get_required_xp`, нужна только для пересчёта уровней

    Args:
        level (int): Уровень пользователя

    Returns:
        int: Требуемое количество опыта по старой формуле
    """
    return (int(100 * level + 50 * level**1.688) + 9) // 10 * 10


def get_total_xp(level: int, xp: int) -> int:
    """### Функция для вычисления всего накопленного опыта по старой формуле
    Суммирует опыт, потраченный на все пройденные уровни, и опыт текущего уровня

    Args:
        level (int): Текущий уровень пользователя
        xp (int): Опыт пользователя на текущем уровне

    Returns:
        int: Весь накопленный опыт
    """
    return sum(get_old_required_xp(lvl) for lvl in range(1, level)) + xp


def get_level_from_total(total_xp: int) -> tuple[int, int]:
    """### Функция для вычисления уровня по всему накопленному опыту по новой формуле
    Повторяет логику повышения уровня из :func:`utils.add_xp`

    Args:
        total_xp (int): Весь накопленный опыт

    Returns:
        tuple[int, int]: (уровень, опыт на этом уровне)
    """
    level = 1
    while total_xp >= get_required_xp(level):
        total_xp -= get_required_xp(level)
        level += 1
    return level, total_xp


async def recalculate_levels(connection: asyncpg.Connection) -> None:
    """### Функция для пересчёта уровней пользователей под новую формулу опыта
    Переводит уровень и опыт каждого пользователя во весь накопленный опыт по старой формуле
    и заново распределяет его по уровням новой формулы. Перед пересчётом создаёт резервную копию user_levels

    Args:
        connection (:class:`asyncpg.Connection`): Соединение с БД
    """
    backup_exists = await connection.fetchval("SELECT to_regclass($1) IS NOT NULL", LEVELS_BACKUP_TABLE)
    if backup_exists:
        print(f'Пересчёт уровней пропущен: таблица {LEVELS_BACKUP_TABLE} уже есть, пересчёт уже выполнялся')
        return

    async with connection.transaction():
        await connection.execute(f'CREATE TABLE {LEVELS_BACKUP_TABLE} AS TABLE user_levels')
        rows = await connection.fetch("SELECT guild_id, user_id, xp, level FROM user_levels")

        updates = []
        changed = 0
        for row in rows:
            new_level, new_xp = get_level_from_total(get_total_xp(row['level'], row['xp']))
            # Новая формула не сложнее старой, поэтому уровень не может уменьшиться
            if new_level < row['level']:
                raise RuntimeError(
                    f'Уровень уменьшился бы ({row["level"]} -> {new_level}), пересчёт отменён')
            if (new_level, new_xp) != (row['level'], row['xp']):
                changed += 1
            updates.append((new_level, new_xp, row['guild_id'], row['user_id']))

        await connection.executemany(
            "UPDATE user_levels SET level = $1, xp = $2 WHERE guild_id = $3 AND user_id = $4",
            updates)

    print(f'Пересчёт уровней завершён: обработано записей - {len(rows)}, изменено - {changed}. '
          f'Резервная копия: {LEVELS_BACKUP_TABLE}')


async def update_db():
    db_pool = await asyncpg.create_pool(dsn=DATABASE_URL)

    async with db_pool.acquire() as connection:
        await connection.execute(UPDATE_SCHEMA)
        await recalculate_levels(connection)
    await db_pool.close()


if __name__ == '__main__':
    asyncio.run(update_db())
