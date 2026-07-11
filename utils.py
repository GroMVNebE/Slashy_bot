import os
import logging
from typing import List, Any, Dict, Literal
import asyncpg
import discord
from dotenv import load_dotenv
import hashlib

# Инициализируем логгер для этого модуля
logger = logging.getLogger('slashy.utils')
# Загружаем переменные окружения из .env файла
load_dotenv()


def get_env(key: str, default: str | None = None) -> str:
    """### Функция для получения значения переменной из .env файла
    Получает значение переменной из .env файла встроенным методом

    Args:
        key (str): Название переменной окружения (например, 'DISCORD_TOKEN')
        default (str | None, optional): Значение по умолчанию, которое будет присвоено, если в .env не найдено значение переменной

    Raises:
        ValueError: Если переменная не найдена в .env файле и не задано значение по умолчанию, выбрасывает исключение с сообщением об ошибке

    Returns:
        str: Значение переменной окружения
    """
    val = os.getenv(key)
    if not val:
        val = default
    if not val:
        raise ValueError(f'Переменная {key} не задана в .env')
    return val


DATABASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_levels (
    guild_id BIGINT,
    user_id BIGINT,
    xp BIGINT DEFAULT 0 CHECK (xp >= 0),
    level INTEGER DEFAULT 1 CHECK (level >= 1),
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS guess_number (
    guild_id BIGINT,
    user_id BIGINT,
    number INTEGER CHECK (number BETWEEN 0 AND 1000),
    tries INTEGER DEFAULT 0 CHECK (tries >= 0),
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS voice_stats (
    guild_id BIGINT,
    user_id BIGINT,
    day DATE DEFAULT CURRENT_DATE,
    seconds INTEGER DEFAULT 0 CHECK (seconds >= 0),
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
CREATE TABLE IF NOT EXISTS voice_max_sessions (
    guild_id BIGINT,
    user_id BIGINT,
    day DATE DEFAULT CURRENT_DATE,
    max_seconds INTEGER DEFAULT 0 CHECK (max_seconds >= 0),
    PRIMARY KEY (guild_id, user_id, day)
);
CREATE TABLE IF NOT EXISTS voice_detailed_sessions (
    session_id BIGSERIAL PRIMARY KEY,
    guild_id TEXT,
    user_id TEXT,
    start_time TIMESTAMP WITH TIME ZONE,
    end_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    seconds INTEGER CHECK (seconds >= 0),
    CONSTRAINT check_timestamp CHECK (end_time > start_time)
);
"""


async def setup_database(pool: asyncpg.Pool) -> None:
    """### Функция для настройки БД при запуске бота
    Создаёт таблицы в случае их отсутствия и обновляет уже существующие, если требуется

    Args:
        pool (:class:`asyncpg.Pool`): Пул соединений с базой данных
    """
    logger.info('Создание таблиц в БД')
    try:
        async with pool.acquire() as connection:
            await connection.execute(DATABASE_SCHEMA)
        logger.info('Таблицы в БД успешно созданы')
    except Exception as e:
        logger.error(f'Ошибка при создании таблиц в БД: {e}', exc_info=True)
        raise e


async def add_xp(user_id: int, guild_id: int, xp: int, pool: asyncpg.Pool) -> None:
    """### Функция начисления опыта пользователю
    Добавляет пользователю переданное кол-во опыта и обновляет его уровень при необходимости
    (текущий опыт >= требуемого)

    Args:
        user_id (int): ID пользователя, которому начисляется опыт
        guild_id (int): ID сервера, на котором состоит пользователь
        xp (int): Количество опыта, которое будет добавлено
        pool (:class:`asyncpg.Pool`): Пул соединений с базой данных

    Raises:
        ValueError: Если было передано не положительное количество опыта
    """
    logger.debug(
        f'Начисление {xp} опыта пользователю {user_id} на сервере {guild_id}')
    if xp <= 0:
        raise ValueError(
            f'Переданное значение добавляемого опыта ({xp}) должно быть положительным!')
    try:
        async with pool.acquire() as connection:
            # Начисляем опыт и получаем обновлённые данные о пользователе
            logger.debug(
                f'Обновление данные об уровне пользователя {user_id} в БД')
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
            logger.debug(
                f'Получение обновлённых данные об уровне пользователя {user_id}')
            curr_xp = row['xp']
            curr_level = row['level']
            # Вычисляем требуемый опыт для следующего уровня
            required_xp = (
                int(100 * curr_level + 50 * curr_level**1.688) + 9) // 10 * 10
            # Поднимаем уровень пользователя, если опыта достаточно
            if curr_xp >= required_xp:
                logger.debug(f'Увеличение уровня пользователя {user_id}')
            level_up = False
            while curr_xp >= required_xp:
                curr_xp -= required_xp
                curr_level += 1
                required_xp = (
                    int(100 * curr_level + 50 * curr_level**1.688) + 9) // 10 * 10
                level_up = True
            # Обновляем уровень и оставшийся опыт в базе данных, если уровень был повышен
            if level_up:
                logger.debug(
                    f'Обновление данных об уровне пользователя {user_id}')
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
        logger.debug(
            f'{get_plural(xp, ("Начислена", "Начислено", "Начислено"))} {xp} ед. опыта пользователю {user_id} на сервере {guild_id}, '
            f'текущий уровень: {curr_level}, текущий опыт: {curr_xp}'
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
        title (str | None, optional): Заголовок Embed. По умолчанию имеет значение None
        description (str | None, optional): Описание содержимого. По умолчанию имеет значение None
        color (:class:`discord.Color`, optional): Цвет Embed. По умолчанию имеет значение discord.Color.blurple()
        thumbnail_url (str | None, optional): URL компактной миниатюры. По умолчанию имеет значение None
        image_url (str | None, optional): URL изображения Embed. По умолчанию имеет значение None
        footer_text (str | None, optional): Текст нижней части Embed. По умолчанию имеет значение None
        footer_icon (str | None, optional): URL иконки нижней части Embed. По умолчанию имеет значение None
        author_name (str | None, optional): Имя автора. По умолчанию имеет значение None
        author_icon (str | None, optional): URL иконки автора. По умолчанию имеет значение None
        fields (List[Dict[str, Any]] | None, optional): Поля с содержимым в формате {"name": "...", "value": "...", "inline": True | False}. По умолчанию имеет значение None
        timestamp (bool, optional): Временная метка (если имеет значение True, в Embed будет добавлено текущее время). По умолчанию имеет значение True

    Returns:
        discord.Embed: Созданный Embed с заданными параметрами
    """
    logger.debug(
        f'Создание Embed с параметрами {title}, {description}, {color}')
    embed = discord.Embed(title=title, description=description, color=color)

    if (any([timestamp, thumbnail_url, image_url, footer_text, author_name, fields])):
        logger.debug(f'Добавление элементов в созданный Embed:\
{"Текущее время " if timestamp else ""} {"Миниатюра " if thumbnail_url else ""}\
{"Изображение " if image_url else ""} {"Завершающий текст (подвал) " if footer_text else ""}\
{"Указание автора " if author_name else ""} {"Иконка автора " if author_icon else ""}\
{"Текстовые поля" if fields else ""}')

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

    logger.debug('Возвращение созданного Embed')
    return embed


def get_info(object: discord.Member | discord.Guild | discord.VoiceChannel | discord.StageChannel | None) -> str:
    """### Функция для получения информации об объекте
    Возвращает хэш ID пользователя/сервера/канала  
    *Используется для упрощения отладки*

    Args:
        object (discord.Member | discord.Guild | discord.VoiceChannel | discord.StageChannel | None): Объект, для которого нужно получить строку с информацией

    Returns:
        str: Строка с информацией о переданном объекте
    """
    logger.debug(f'Получение строки с информацией для объекта {type(object)}')
    if object is None:
        return 'Нет данных'
    bytes = object.id.to_bytes(
        (object.id.bit_length() + 7) // 8 or 1, byteorder='big')
    hash_code = hashlib.sha256(bytes).hexdigest()
    return f'{hash_code}'


async def create_default_user_settings(pool: asyncpg.Pool, member: discord.Member) -> None:
    """### Функция для создания записи со стандартными настройками пользователя
    Создаёт в БД запись со стандартными настройками пользователя

    Args:
        pool (:class:`asyncpg.Pool`): Пул соединений с БД
        member (:class:`discord.Member`): Пользователь, для которого требуется создать запись со стандартными настройками
    """
    logger.debug(
        f'Создание настроек по умолчанию для пользователя {member.id}')
    try:
        async with pool.acquire() as con:
            logger.debug(f'Получение настроек для сервера {member.guild.id}')
            guild_setting = await con.fetchrow(
                """
                SELECT vc_stats_enabled
                FROM guild_settings
                WHERE guild_id = $1
            """,
                member.guild.id
            )
            default = True if guild_setting else False
            logger.debug(
                'Создание записи со стандартными настройками пользователя')
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
    except Exception as e:
        logger.error(
            f'Ошибка при создании записи со стандартными настройками пользователя {member.id}: {e}', exc_info=True)
        raise e


async def create_default_guild_settings(pool: asyncpg.Pool, guild: discord.Guild) -> None:
    """### Функция для создания записи со стандартными настройками сервера
    Создаёт запись в БД со стандартными настройками сервера

    Args:
        pool (:class:`asyncpg.Pool`): Пул соединений с БД
        guild (:class:`discord.Guild`): Сервер, для которого требуется создать запись с настройками
    """
    logger.debug(
        f'Создание записи со стандартными настройками сервера для сервера {guild.id}')
    try:
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO guild_settings (guild_id, only_owner_access, vc_stats_enabled)
                VALUES ($1, $2, $3)
            """,
                guild.id,
                True,
                False
            )
    except Exception as e:
        logger.error(
            f'Ошибка при создании записи со стандартными настройками сервера {guild.id}')
        raise e


def get_plural(val: int, forms: tuple[str, str, str]) -> str:
    """### Функция для получения правильной формы множественного числа
    Возвращает правильную форму множественного числа для переданного значения (пр. 1 единица, 2 единицы, 5 единиц)

    Args:
        val (int): Значение, для которого нужно получить форму множественного числа
        forms (tuple[str, str, str]): Возможные формы множественного числа

    Returns:
        str: Подходящая форма множественного числа
    """
    logger.debug(
        f'Выбор подходящей формы множественного числа из {forms} для {val}')
    val = abs(val) % 100
    if 11 <= val <= 19:
        logger.debug(f'Выбрана форма {forms[2]}')
        return forms[2]
    n = val % 10
    if n == 1:
        logger.debug(f'Выбрана форма {forms[0]}')
        return forms[0]
    if 2 <= n <= 4:
        logger.debug(f'Выбрана форма {forms[1]}')
        return forms[1]
    logger.debug(f'Выбрана форма {forms[2]}')
    return forms[2]


def readable_time(seconds: int) -> str | Literal['Время не указано']:
    """### Функция для перевода времени в секундах в удобный для чтения формат
    Конвертирует время в секундах в строку со временем в часах, минутах и секундах

    Returns:
        str: Строка со временем в часах, минутах и секундах *или* "Время не указано", если было передано недопустимое время
    """
    logger.debug(f'Конвертация {seconds} сек. в удобную для чтения строку')
    if seconds <= 0:
        logger.debug(
            'Было передано некорректное значение - возвращена строка "Время не указано"')
        return 'Время не указано'

    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)

    logger.debug(
        f'Результат конвертации: {hours} ч. {minutes} мин. {seconds} сек.')
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
