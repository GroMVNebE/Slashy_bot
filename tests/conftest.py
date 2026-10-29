import os
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import asyncpg
import pytest

# Задаём тестовые значения до импорта модулей проекта
# Модули проекта (utils, cogs) нельзя импортировать в начале этого файла: utils читает перец при импорте,
# а автосортировка импортов в редакторе переносит такие импорты выше этих строк. Поэтому импорт utils - внутри фикстуры
os.environ['VOICE_PEPPER'] = 'test-voice-pepper'
os.environ['LOG_PEPPER'] = 'test-log-pepper'
# CREATOR_ID читается при импорте cogs/admin.py
os.environ['CREATOR_ID'] = '1000'


TABLES = ('user_levels', 'guess_number', 'voice_stats', 'guild_settings',
          'user_settings', 'voice_max_sessions', 'voice_detailed_sessions')


def _db_key(url: str) -> tuple:
    """### Функция для получения адреса БД без учётных данных
    Нужна, чтобы сравнивать TEST_DATABASE_URL и DATABASE_URL независимо от пользователя и пароля"""
    parsed = urlparse(url)
    return (parsed.hostname, parsed.port or 5432, parsed.path.strip('/'))


@pytest.fixture
async def db_pool():
    """### Пул соединений с тестовой БД
    Перед каждым тестом создаёт таблицы и очищает их. Тест пропускается, если TEST_DATABASE_URL не задан"""
    # Импорт utils также загружает .env, откуда берутся TEST_DATABASE_URL и DATABASE_URL
    from utils import setup_database
    test_url = os.getenv('TEST_DATABASE_URL')
    if not test_url:
        pytest.skip('TEST_DATABASE_URL не задан - тесты с БД пропущены')
    main_url = os.getenv('DATABASE_URL')
    # Защита от случайной очистки рабочей БД
    if main_url and _db_key(test_url) == _db_key(main_url):
        pytest.fail('TEST_DATABASE_URL указывает на рабочую БД (DATABASE_URL). '
                    'Укажите отдельную тестовую базу')
    pool = await asyncpg.create_pool(dsn=test_url)
    await setup_database(pool)
    async with pool.acquire() as con:
        await con.execute(f'TRUNCATE {", ".join(TABLES)} RESTART IDENTITY')
    yield pool
    await pool.close()


@pytest.fixture
def bot(db_pool):
    """### Заглушка бота с настоящим пулом соединений с тестовой БД"""
    bot = MagicMock()
    bot.db_pool = db_pool
    bot.get_cog.return_value = None
    bot.wait_until_ready = AsyncMock()
    return bot
