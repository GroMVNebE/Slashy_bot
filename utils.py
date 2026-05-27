import os
import logging
from typing import List, Any, Dict, TYPE_CHECKING
import asyncpg
import discord
from dotenv import load_dotenv

if TYPE_CHECKING:
    from launch import Bot

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
        logger.error(f'Переменная {key} не задана в .env')
        raise ValueError(f'Переменная {key} не задана в .env')
    return value


DATABASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_levels (
    guild_id BIGINT,
    user_id BIGINT,
    xp BIGINT DEFAULT 0,
    level INTEGER DEFAULT 1,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS guess_number (
    guild_id BIGINT,
    user_id BIGINT,
    number INTEGER,
    tries INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS voice_stats (
    guild_id BIGINT,
    user_id BIGINT,
    day DATE DEFAULT CURRENT_DATE,
    seconds INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, day)
);
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id BIGINT,
    only_owner_access BOOLEAN,
    vc_stats_enabled BOOLEAN,
    PRIMARY KEY (guild_id)
);
CREATE TABLE IF NOT EXISTS user_settings (
    guild_id BIGINT,
    user_id BIGINT,
    vc_stats_enabled BOOLEAN,
    vc_stats_privacy BOOLEAN,
    PRIMARY KEY (guild_id, user_id)
);
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS vc_stats_privacy BOOLEAN;
"""
# ALTER TABLE добавлен для корректного обновления БД, в дальнейшем его нужно удалить


async def setup_database(pool: asyncpg.Pool):
    """Создаёт необходимые таблицы в базе данных

    Args:
        pool (asyncpg.Pool): Пул соединений с базой данных
    """
    logger.debug('Настройка базы данных (создание таблиц, если их нет)')
    try:
        async with pool.acquire() as connection:
            await connection.execute(DATABASE_SCHEMA)
        logger.info('Настройка БД завершена успешно')
    except Exception as e:
        logger.error(f'Ошибка при настройке базы данных: {e}', exc_info=True)
        raise e


async def add_xp(user_id: int, guild_id: int, xp: int, pool: asyncpg.Pool):
    """### Функция начисления опыта пользователю
    Добавляет пользователю переданное кол-во опыта и обновляет его уровень при необходимости
    (текущий опыт >= требуемого)

    Args:
        user_id (int): ID пользователя
        guild_id (int): ID сервера
        xp (int): Количество опыта для добавления
        pool (asyncpg.Pool): Пул соединений с базой данных
    """
    logger.debug(
        f'Попытка начислить {xp} опыта пользователю {user_id} на {guild_id}')
    try:
        async with pool.acquire() as connection:
            # Начисляем опыт и получаем обновлённые данные о пользователе
            row = await connection.fetchrow(
                """
                INSERT INTO user_levels (guild_id, user_id, xp)
                VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, user_id) DO UPDATE
                SET xp = user_levels.xp + EXCLUDED.xp
                RETURNING xp, level
            """,
                guild_id,
                user_id,
                xp,
            )
            # Получаем данные о текущем опыте и уровне пользователя
            curr_xp = row['xp']
            curr_level = row['level']
            # Вычисляем требуемый опыт для следующего уровня
            required_xp = (
                int(100 * curr_level + 50 * curr_level**1.688) + 9) // 10 * 10
            # Поднимаем уровень пользователя, если опыта достаточно
            level_up = False
            while curr_xp >= required_xp:
                # Поднимаем уровень пользователя, отнимая требуемый опыт
                curr_xp -= required_xp
                curr_level += 1
                required_xp = (
                    int(100 * curr_level + 50 * curr_level**1.688) + 9) // 10 * 10
                level_up = True
            # Обновляем уровень и оставшийся опыт в базе данных, если уровень был повышен
            if level_up:
                await connection.execute(
                    """
                    UPDATE user_levels
                    SET level = $1, xp = $2
                    WHERE guild_id = $3 AND user_id = $4
                """,
                    curr_level,
                    curr_xp,
                    guild_id,
                    user_id,
                )
        logger.info(
            f'Начислено {xp} опыт пользователю {user_id} на сервере {guild_id}, текущий уровень: {curr_level}, текущий опыт: {curr_xp}'
        )
    except Exception as e:
        logger.error(f'Ошибка при начислении опыта: {e}', exc_info=True)
        raise e


def create_embed(
    title: str | None = None,
    description: str | None = None,
    color: discord.Color = discord.Color.blurple(),
    thumbnail_url: str | None = None,
    image_url: str | None = None,
    footer_text: str | None = None,
    footer_icon: str | None = None,
    author_name: str | None = None,
    author_icon: str | None = None,
    fields: List[Dict[str, Any]] | None = None,
    timestamp: bool = True,
) -> discord.Embed:
    """### Функция для создания Embed
    Создаёт Embed, используя переданные параметры и стандартный конструктор

    Args:
        title (str | None, optional): Заголовок Embed. По умолчанию имеет значение None.
        description (str | None, optional): Описание содержимого. По умолчанию имеет значение None.
        color (discord.Color, optional): Цвет Embed. По умолчанию имеет значение discord.Color.blurple().
        thumbnail_url (str | None, optional): URL компактной миниатюры. По умолчанию имеет значение None.
        image_url (str | None, optional): URL изображения Embed. По умолчанию имеет значение None.
        footer_text (str | None, optional): Текст нижней части Embed. По умолчанию имеет значение None.
        footer_icon (str | None, optional): URL иконки нижней части Embed. По умолчанию имеет значение None.
        author_name (str | None, optional): Имя автора. По умолчанию имеет значение None.
        author_icon (str | None, optional): URL иконки автора. По умолчанию имеет значение None.
        fields (List[Dict[str, Any]] | None, optional): Поля с содержимым в формате {"name": "...", "value": "...", "inline": True | False}. По умолчанию имеет значение None.
        timestamp (bool, optional): Временная метка (если имеет значение True, в Embed будет добавлено текущее время). По умолчанию имеет значение True.

    Returns:
        discord.Embed: Созданный Embed с заданными параметрами
    """

    embed = discord.Embed(title=title, description=description, color=color)

    if timestamp:
        embed.timestamp = discord.utils.utcnow()

    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    if image_url:
        embed.set_image(url=image_url)

    if footer_text:
        embed.set_footer(text=footer_text, icon_url=footer_icon)

    if author_name:
        embed.set_author(name=author_name, icon_url=author_icon)

    if fields:
        for field in fields:
            embed.add_field(
                name=field.get('name', 'Заголовок поля по умолчанию'),
                value=field.get('value', 'Значение поля по умолчанию'),
                inline=field.get('inline', True),
            )

    return embed


def user_data(interaction: discord.Interaction):
    """### Функция для получения данных о пользователе, вызвавшем взаимодействие
    Возвращает строку с id пользователя и его отображаемым именем

    Args:
        interaction (discord.Interaction): Объект взаимодействия, содержащий подробные данные об отправленной команде

    Returns:
        str: Строка с id пользователя и отображаемым именем
    """
    return f'{interaction.user.id} ({interaction.user.display_name})'


def server_data(interaction: discord.Interaction):
    """### Функция для получения данных о сервере, на котором было вызвано взаимодействие
    Возвращает строку с id сервера и его названием

    Args:
        interaction (discord.Interaction): Объект взаимодействия, содержащий подробные данные об отправленной команде

    Returns:
        str: Строка с id сервера и названием или пустая строка, если нет данных о сервере
    """
    if not interaction.guild:
        return 'Нет данных о сервере'
    return f'{interaction.guild_id} ({interaction.guild.name})'


async def create_default_user_settings(bot: 'Bot', member: discord.Member):
    if not bot.db_pool:
        return
    async with bot.db_pool.acquire() as con:
        guild_setting = await con.fetchrow(
            """
            SELECT vc_stats_enabled
            FROM guild_settings
            WHERE guild_id = $1
        """,
            member.guild.id
        )
        default = True if guild_setting else False
        await con.execute(
            """
            INSERT INTO user_settings (guild_id, user_id, vc_stats_enabled, vc_stats_privacy)
            VALUES ($1, $2, $3, $4)
        """,
            member.guild.id,
            member.id,
            default,
            True
        )


async def create_default_guild_settings(bot: 'Bot', interaction: discord.Interaction):
    if not bot.db_pool:
        return
    async with bot.db_pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO guild_settings (guild_id, only_owner_access, vc_stats_enabled)
            VALUES ($1, $2, $3)
        """,
            interaction.guild_id,
            True,
            False
        )


def get_plural(val: int, forms: tuple[str, str, str]):
    val = abs(val) % 100
    if 11 <= val <= 19:
        return forms[2]
    n = val % 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def readable_time(seconds: int):
    if seconds <= 0:
        return 'Время не указано'

    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)

    parts = []
    if hours > 0:
        parts.append(f"{hours} {get_plural(hours, ('час', 'часа', 'часов'))}")
    if minutes > 0:
        parts.append(
            f"{minutes} {get_plural(minutes, ('минута', 'минуты', 'минут'))}")
    if seconds > 0:
        parts.append(
            f"{seconds} {get_plural(seconds, ('секунда', 'секунды', 'секунд'))}")

    return ' '.join(parts)
