import time
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import discord
import pytest

from cogs.events import ATTACHMENT_ONLY_XP, Events, _message_curve
from factories import (count_rows, insert_guild_settings, insert_user_settings, make_guild,
                       make_member, make_voice_channel, make_voice_state)
from utils import get_voice_hashes

MSK = ZoneInfo('Europe/Moscow')


@pytest.fixture
async def events_cog(bot):
    """### Ког Events без фоновых задач"""
    cog = Events(bot)
    cog.save_sessions_task.cancel()
    cog.delete_pending_sessions_task.cancel()
    cog.delete_old_sessions_task.cancel()
    return cog


def make_session(duration: int, guild_id: int = 10, left_at: float | None = None) -> dict:
    """### Сессия "общения" заданной длительности, закончившаяся в left_at"""
    left_at = left_at or time.time()
    return {'prd_start': left_at - duration,
            'session_start': datetime.fromtimestamp(left_at - duration, MSK),
            'guild_id': guild_id,
            'left_at': left_at}


# === start_tracking / stop_tracking ===
async def test_start_tracking_creates_session(events_cog):
    await events_cog.start_tracking(make_member(1, 10))
    session = events_cog.active_sessions[1]
    assert session['guild_id'] == 10
    assert {'prd_start', 'session_start'} <= session.keys()


async def test_start_tracking_keeps_existing_session(events_cog):
    member = make_member(1, 10)
    await events_cog.start_tracking(member)
    first = dict(events_cog.active_sessions[1])
    await events_cog.start_tracking(member)
    assert events_cog.active_sessions[1] == first


async def test_stop_tracking_moves_to_pending(events_cog):
    member = make_member(1, 10)
    await events_cog.start_tracking(member)
    await events_cog.stop_tracking(member)
    assert 1 not in events_cog.active_sessions
    session = events_cog.pending_sessions[1]
    assert session['expires_at'] - session['left_at'] == pytest.approx(180, abs=1)


async def test_start_tracking_restores_pending(events_cog):
    member = make_member(1, 10)
    await events_cog.start_tracking(member)
    session_start = events_cog.active_sessions[1]['session_start']
    await events_cog.stop_tracking(member)
    await events_cog.start_tracking(member)
    assert 1 not in events_cog.pending_sessions
    session = events_cog.active_sessions[1]
    assert session['session_start'] == session_start
    assert 'expires_at' not in session and 'left_at' not in session


# === get_valid_voice_members ===
async def test_valid_members_without_filter(events_cog):
    guild = make_guild(10)
    user = make_member(1, guild=guild)
    bot_member = make_member(2, guild=guild, bot=True)
    deaf = make_member(3, guild=guild, deaf=True)
    channel = make_voice_channel([user, bot_member, deaf], guild)
    assert await events_cog.get_valid_voice_members(channel) == [user]


async def test_valid_members_guild_disabled(events_cog, db_pool):
    await insert_guild_settings(db_pool, 10, vc_stats_enabled=False)
    guild = make_guild(10)
    channel = make_voice_channel([make_member(1, guild=guild), make_member(2, guild=guild)], guild)
    assert await events_cog.get_valid_voice_members(channel, filtered=True) == []


async def test_valid_members_respects_user_settings(events_cog, db_pool):
    await insert_guild_settings(db_pool, 10)
    await insert_user_settings(db_pool, user_id=2, vc_stats_enabled=False)
    guild = make_guild(10)
    allowed = make_member(1, guild=guild)  # без записи настроек - сбор разрешён
    opted_out = make_member(2, guild=guild)
    channel = make_voice_channel([allowed, opted_out], guild)
    assert await events_cog.get_valid_voice_members(channel, filtered=True) == [allowed]


# === on_voice_state_update ===
async def test_voice_join_and_leave(events_cog, db_pool):
    await insert_guild_settings(db_pool, 10)
    guild = make_guild(10)
    first, second = make_member(1, guild=guild), make_member(2, guild=guild)
    channel = make_voice_channel([first, second], guild)

    await events_cog.on_voice_state_update(second, make_voice_state(None), make_voice_state(channel))
    assert set(events_cog.active_sessions) == {1, 2}

    channel.members = [first]
    await events_cog.on_voice_state_update(second, make_voice_state(channel), make_voice_state(None))
    assert events_cog.active_sessions == {}
    assert set(events_cog.pending_sessions) == {1, 2}


async def test_voice_single_member_not_tracked(events_cog, db_pool):
    await insert_guild_settings(db_pool, 10)
    guild = make_guild(10)
    member = make_member(1, guild=guild)
    channel = make_voice_channel([member], guild)
    await events_cog.on_voice_state_update(member, make_voice_state(None), make_voice_state(channel))
    assert events_cog.active_sessions == {}


async def test_voice_bot_ignored(events_cog):
    guild = make_guild(10)
    bot_member = make_member(2, guild=guild, bot=True)
    channel = make_voice_channel([make_member(1, guild=guild), bot_member], guild)
    await events_cog.on_voice_state_update(bot_member, make_voice_state(None), make_voice_state(channel))
    assert events_cog.active_sessions == {}


# === save_time ===
async def test_save_time_short_session_skipped(events_cog, db_pool):
    await events_cog.save_time(1, make_session(10), autosave=False)
    assert await count_rows(db_pool, 'voice_stats') == 0


async def test_save_time_autosave_only_stats(events_cog, db_pool):
    await events_cog.save_time(1, make_session(600), autosave=True)
    assert await db_pool.fetchval("SELECT seconds FROM voice_stats WHERE user_id = 1") == 600
    assert await count_rows(db_pool, 'voice_max_sessions') == 0
    assert await count_rows(db_pool, 'user_levels') == 0


async def test_save_time_final_with_detailed(events_cog, db_pool):
    await insert_user_settings(db_pool, user_id=1, detailed=True)
    await events_cog.save_time(1, make_session(70 * 60), autosave=False)
    assert await db_pool.fetchval("SELECT seconds FROM voice_stats WHERE user_id = 1") == 70 * 60
    assert await db_pool.fetchval("SELECT max_seconds FROM voice_max_sessions WHERE user_id = 1") == 70 * 60
    uid_hash, gid_hash = get_voice_hashes(1, 10)
    row = await db_pool.fetchrow("SELECT user_id, guild_id, seconds FROM voice_detailed_sessions")
    assert (row['user_id'], row['guild_id'], row['seconds']) == (uid_hash, gid_hash, 70 * 60)
    # 70 минут - опорная точка кривой опыта за сессии (30 опыта)
    assert await db_pool.fetchval("SELECT xp FROM user_levels WHERE user_id = 1") == 30


async def test_save_time_short_session_without_xp(events_cog, db_pool):
    # Сессия короче 10 минут сохраняется, но опыт за неё не начисляется
    await events_cog.save_time(1, make_session(5 * 60), autosave=False)
    assert await db_pool.fetchval("SELECT seconds FROM voice_stats WHERE user_id = 1") == 5 * 60
    assert await count_rows(db_pool, 'user_levels') == 0


async def test_save_time_without_detailed_permission(events_cog, db_pool):
    await insert_user_settings(db_pool, user_id=1, detailed=False)
    await events_cog.save_time(1, make_session(70 * 60), autosave=False)
    assert await count_rows(db_pool, 'voice_stats') == 1
    assert await count_rows(db_pool, 'voice_detailed_sessions') == 0


# === delete_pending_sessions_task ===
async def test_delete_pending_sessions(events_cog, db_pool):
    now = time.time()
    expired = make_session(600, left_at=now - 200)
    expired['expires_at'] = now - 20
    waiting = make_session(600, left_at=now - 10)
    waiting['expires_at'] = now + 170
    events_cog.pending_sessions = {1: expired, 2: waiting}

    await events_cog.delete_pending_sessions_task()

    assert set(events_cog.pending_sessions) == {2}
    assert await db_pool.fetchval("SELECT COUNT(*) FROM voice_stats WHERE user_id = 1") == 1
    assert await db_pool.fetchval("SELECT COUNT(*) FROM voice_stats WHERE user_id = 2") == 0


# === on_message ===
def make_message(author, content: str) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.author = author
    message.content = content
    return message


async def test_on_message_text_xp(events_cog, db_pool):
    content = 'Привет всем, как дела?'
    await events_cog.on_message(make_message(make_member(1, 10), content))
    assert await db_pool.fetchval("SELECT xp FROM user_levels WHERE user_id = 1") == _message_curve(len(content))


async def test_on_message_attachment_only(events_cog, db_pool):
    await events_cog.on_message(make_message(make_member(1, 10), ''))
    assert await db_pool.fetchval("SELECT xp FROM user_levels WHERE user_id = 1") == ATTACHMENT_ONLY_XP


async def test_on_message_short_text_without_xp(events_cog, db_pool):
    # Сообщение короче порога не даёт опыта и не вызывает исключения
    await events_cog.on_message(make_message(make_member(1, 10), 'ок'))
    assert await count_rows(db_pool, 'user_levels') == 0


async def test_on_message_from_bot_ignored(events_cog, db_pool):
    await events_cog.on_message(make_message(make_member(1, 10, bot=True), 'Сообщение бота'))
    assert await count_rows(db_pool, 'user_levels') == 0
