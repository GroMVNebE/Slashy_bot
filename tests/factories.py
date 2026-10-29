"""### Фабрики заглушек Discord и хелперы для заполнения тестовой БД"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import discord

from utils import get_voice_hashes


def make_guild(guild_id: int = 10) -> MagicMock:
    """### Заглушка сервера Discord"""
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    return guild


def make_member(user_id: int = 1, guild_id: int = 10, display_name: str = 'Тестер',
                bot: bool = False, deaf: bool = False, guild: MagicMock | None = None) -> MagicMock:
    """### Заглушка участника сервера
    Проходит проверку isinstance(member, discord.Member)"""
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = display_name
    member.bot = bot
    member.guild = guild or make_guild(guild_id)
    member.voice = MagicMock(spec=discord.VoiceState)
    member.voice.deaf = False
    member.voice.self_deaf = deaf
    return member


def make_user(user_id: int = 1) -> MagicMock:
    """### Заглушка пользователя вне сервера (не проходит проверку на discord.Member)"""
    user = MagicMock(spec=discord.User)
    user.id = user_id
    return user


def make_interaction(user: MagicMock) -> MagicMock:
    """### Заглушка взаимодействия
    Все методы ответа - AsyncMock, их вызовы можно проверять через call_args"""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = user
    interaction.guild = getattr(user, 'guild', None)
    interaction.guild_id = interaction.guild.id if interaction.guild else None
    interaction.response = MagicMock()
    for method in ('send_message', 'edit_message', 'defer', 'send_modal'):
        setattr(interaction.response, method, AsyncMock())
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    interaction.delete_original_response = AsyncMock()
    return interaction


def make_voice_channel(members: list, guild: MagicMock, channel_id: int = 100) -> MagicMock:
    """### Заглушка голосового канала с заданными участниками"""
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = channel_id
    channel.guild = guild
    channel.members = members
    return channel


def make_voice_state(channel: MagicMock | None, deaf: bool = False) -> MagicMock:
    """### Заглушка состояния голосового подключения"""
    state = MagicMock(spec=discord.VoiceState)
    state.channel = channel
    state.deaf = False
    state.self_deaf = deaf
    return state


def sent_kwargs(mock: AsyncMock) -> dict:
    """### Именованные аргументы последнего вызова метода ответа"""
    assert mock.await_count > 0, 'Метод ответа не был вызван'
    return mock.call_args.kwargs


async def insert_guild_settings(pool: asyncpg.Pool, guild_id: int = 10,
                                vc_stats_enabled: bool = True) -> None:
    await pool.execute(
        "INSERT INTO guild_settings (guild_id, only_owner_access, vc_stats_enabled) VALUES ($1, TRUE, $2)",
        guild_id, vc_stats_enabled)


async def insert_user_settings(pool: asyncpg.Pool, user_id: int = 1, guild_id: int = 10,
                               vc_stats_enabled: bool = True, detailed: bool = False) -> None:
    await pool.execute(
        """
        INSERT INTO user_settings (guild_id, user_id, vc_stats_enabled, vc_stats_privacy, vc_detailed_stats_enabled)
        VALUES ($1, $2, $3, TRUE, $4)
        """,
        guild_id, user_id, vc_stats_enabled, detailed)


async def insert_detailed_session(pool: asyncpg.Pool, user_id: int, guild_id: int,
                                  start: datetime, end: datetime) -> None:
    uid_hash, gid_hash = get_voice_hashes(user_id, guild_id)
    await pool.execute(
        """
        INSERT INTO voice_detailed_sessions (user_id, guild_id, start_time, end_time, seconds)
        VALUES ($1, $2, $3, $4, $5)
        """,
        uid_hash, gid_hash, start, end, int((end - start).total_seconds()))


async def count_rows(pool: asyncpg.Pool, table: str) -> int:
    return await pool.fetchval(f'SELECT COUNT(*) FROM {table}')
