from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord
import pytest

from cogs.general import General, delete_user_data
from factories import (count_rows, insert_detailed_session, insert_guild_settings,
                       insert_user_settings, make_interaction, make_member, make_user, sent_kwargs)
from utils import get_voice_hashes

MSK = ZoneInfo('Europe/Moscow')


@pytest.fixture
def offline_bot():
    """### Заглушка бота без БД для команд, которые к ней не обращаются"""
    return MagicMock()


@pytest.fixture
def general_cog(bot):
    return General(bot)


# === /rand ===
async def test_rand_equal_bounds(offline_bot):
    cog = General(offline_bot)
    interaction = make_interaction(make_member())
    await cog.rand.callback(cog, interaction, 5, 5)
    kwargs = sent_kwargs(interaction.response.send_message)
    assert kwargs['embed'].title == 'Некорректный ввод!'
    assert kwargs['embed'].color == discord.Color.red()


async def test_rand_swapped_bounds(offline_bot, monkeypatch):
    monkeypatch.setattr('cogs.general.randint', lambda start, end: 42)
    cog = General(offline_bot)
    interaction = make_interaction(make_member())
    await cog.rand.callback(cog, interaction, 10, 1)
    embed = sent_kwargs(interaction.response.send_message)['embed']
    assert '42' in embed.description
    assert embed.footer.text == 'Диапазон: [1; 10]'


async def test_rand_not_member(offline_bot):
    cog = General(offline_bot)
    interaction = make_interaction(make_user())
    await cog.rand.callback(cog, interaction, 1, 10)
    interaction.response.send_message.assert_not_awaited()


# === /guess ===
async def test_guess_creates_game(general_cog, db_pool):
    interaction = make_interaction(make_member(1, 10))
    await general_cog.guess.callback(general_cog, interaction)
    row = await db_pool.fetchrow("SELECT number, tries FROM guess_number WHERE user_id = 1")
    assert 1 <= row['number'] <= 1000 and row['tries'] == 0
    kwargs = sent_kwargs(interaction.response.send_message)
    assert isinstance(kwargs['view'], General.GuessView)


async def test_guess_continues_game(general_cog, db_pool):
    await db_pool.execute("INSERT INTO guess_number VALUES (10, 1, 500, 3)")
    interaction = make_interaction(make_member(1, 10))
    await general_cog.guess.callback(general_cog, interaction)
    embed = sent_kwargs(interaction.response.send_message)['embed']
    assert 'уже есть' in embed.description
    assert embed.footer.text == 'Текущих попыток: 3'
    assert await count_rows(db_pool, 'guess_number') == 1


async def submit_guess(pool, value: str):
    """### Отправляет число в модальное окно игры и возвращает (interaction, view)"""
    interaction = make_interaction(make_member(1, 10))
    view = General.GuessView(pool, interaction)
    modal = General.GuessModal(view)
    modal.guess_input._value = value
    await modal.on_submit(interaction)
    return interaction, view


@pytest.mark.parametrize('value', ['abc', '0', '1001'])
async def test_guess_invalid_input(db_pool, value):
    interaction, _ = await submit_guess(db_pool, value)
    assert sent_kwargs(interaction.response.edit_message)['embed'].title == 'Некорректный ввод!'


@pytest.mark.parametrize('value, hint', [('300', 'больше'), ('700', 'меньше')])
async def test_guess_miss(db_pool, value, hint):
    await db_pool.execute("INSERT INTO guess_number VALUES (10, 1, 500, 0)")
    interaction, _ = await submit_guess(db_pool, value)
    embed = sent_kwargs(interaction.response.edit_message)['embed']
    assert f'Загаданное число {hint}' in embed.description
    assert await db_pool.fetchval("SELECT tries FROM guess_number WHERE user_id = 1") == 1


async def test_guess_win(db_pool):
    await db_pool.execute("INSERT INTO guess_number VALUES (10, 1, 500, 0)")
    interaction, view = await submit_guess(db_pool, '500')
    kwargs = sent_kwargs(interaction.response.edit_message)
    assert kwargs['embed'].title == 'Вы угадали!'
    assert kwargs['view'] is None
    assert view.is_finished()
    assert await count_rows(db_pool, 'guess_number') == 0
    # С первой попытки начисляется 300 опыта: 150 уходит на 1-й уровень, 150 остаётся
    row = await db_pool.fetchrow("SELECT xp, level FROM user_levels WHERE user_id = 1")
    assert (row['xp'], row['level']) == (150, 2)


# === /sessions: get_collection_warning ===
def sessions_view(bot, user=None):
    user = user or make_member(1, 10)
    return General.VoiceDetailedSessionsView(bot, user, make_interaction(user))


async def test_collection_warning_guild_disabled(bot):
    assert 'На этом сервере' in await sessions_view(bot).get_collection_warning()


async def test_collection_warning_user_disabled(bot, db_pool):
    await insert_guild_settings(db_pool, 10)
    await insert_user_settings(db_pool, 1, 10, vc_stats_enabled=False, detailed=True)
    assert 'Вы отключили' in await sessions_view(bot).get_collection_warning()


@pytest.mark.parametrize('has_user_row', [True, False])
async def test_collection_warning_detailed_disabled(bot, db_pool, has_user_row):
    await insert_guild_settings(db_pool, 10)
    if has_user_row:
        await insert_user_settings(db_pool, 1, 10, detailed=False)
    assert 'Сбор детальных сессий' in await sessions_view(bot).get_collection_warning()


async def test_collection_warning_all_enabled(bot, db_pool):
    await insert_guild_settings(db_pool, 10)
    await insert_user_settings(db_pool, 1, 10, detailed=True)
    assert await sessions_view(bot).get_collection_warning() is None


async def test_collection_warning_db_error(offline_bot):
    offline_bot.db_pool.acquire.side_effect = RuntimeError('БД недоступна')
    assert await sessions_view(offline_bot).get_collection_warning() is None


# === /sessions: get_daily_sessions ===
async def test_daily_sessions_clipped_to_day(bot, db_pool):
    day = date(2026, 10, 20)
    midnight = datetime.combine(day, datetime.min.time(), tzinfo=MSK)
    # Сессия через полночь: в выбранный день попадает только 1 час
    await insert_detailed_session(db_pool, 1, 10, midnight - timedelta(hours=1), midnight + timedelta(hours=1))
    # Сессия другого пользователя и сессия в другой день не учитываются
    await insert_detailed_session(db_pool, 2, 10, midnight + timedelta(hours=5), midnight + timedelta(hours=6))
    await insert_detailed_session(db_pool, 1, 10, midnight + timedelta(days=2), midnight + timedelta(days=2, hours=1))

    sessions, total_seconds = await sessions_view(bot).get_daily_sessions(day)
    assert len(sessions) == 1
    assert total_seconds == 3600


# === /sessions: build_ssn_embed ===
async def test_build_ssn_embed_without_warning(offline_bot):
    embed = sessions_view(offline_bot).build_ssn_embed(date(2026, 10, 20), 5400)
    assert embed.fields == []
    assert '20.10.2026' in embed.description
    assert '1.50 ч.' in embed.description


async def test_build_ssn_embed_with_warning(offline_bot):
    view = sessions_view(offline_bot)
    view.collection_warning = 'Причина'
    embed = view.build_ssn_embed(date(2026, 10, 20), 0)
    assert embed.color == discord.Color.orange()
    assert embed.fields[0].value == 'Причина'


# === /sessions: кнопки ===
async def test_sessions_buttons(offline_bot):
    view = sessions_view(offline_bot)
    view.update_message = AsyncMock()
    interaction = make_interaction(view.user)

    for _ in range(30):
        await view.prev_day.callback(interaction)
    assert view.day_offset == -30
    assert view.prev_day.disabled is True

    await view.next_day.callback(interaction)
    assert view.day_offset == -29
    assert view.prev_day.disabled is False

    await view.today_day.callback(interaction)
    assert view.day_offset == 0
    assert view.update_message.await_count == 32


# === /sessions: команда ===
async def test_sessions_command(general_cog, db_pool):
    interaction = make_interaction(make_member(1, 10))
    await general_cog.sessions.callback(general_cog, interaction)
    kwargs = sent_kwargs(interaction.response.send_message)
    assert kwargs['ephemeral'] is True
    assert isinstance(kwargs['file'], discord.File)
    assert isinstance(kwargs['view'], General.VoiceDetailedSessionsView)
    # Сбор на сервере не настроен - в Embed есть предупреждение
    assert kwargs['embed'].fields[0].name == '⚠️ Сбор сессий отключен'


# === /privacy: delete_user_data ===
async def fill_user_data(pool):
    for guild_id in (10, 20):
        await pool.execute("INSERT INTO user_levels (guild_id, user_id, xp) VALUES ($1, 1, 50)", guild_id)
        await pool.execute("INSERT INTO voice_stats (guild_id, user_id, seconds) VALUES ($1, 1, 600)", guild_id)
        start = datetime.now(MSK) - timedelta(hours=2)
        await insert_detailed_session(pool, 1, guild_id, start, start + timedelta(hours=1))
    await pool.execute("INSERT INTO voice_max_sessions (guild_id, user_id, max_seconds) VALUES (10, 1, 600)")
    # Данные другого пользователя не должны удаляться
    await pool.execute("INSERT INTO user_levels (guild_id, user_id, xp) VALUES (10, 2, 50)")
    await pool.execute("INSERT INTO voice_stats (guild_id, user_id, seconds) VALUES (10, 2, 600)")


async def test_delete_user_data_current_guild(bot, db_pool):
    await fill_user_data(db_pool)
    deleted = await delete_user_data(bot, 1, {'levels', 'voice'}, guild_id=10)
    # user_levels + voice_stats + voice_max_sessions + детальная сессия на сервере 10
    assert deleted == 4
    assert await db_pool.fetchval("SELECT COUNT(*) FROM user_levels WHERE user_id = 1") == 1
    uid_20, _ = get_voice_hashes(1, 20)
    assert await db_pool.fetchval("SELECT user_id FROM voice_detailed_sessions") == uid_20
    assert await db_pool.fetchval("SELECT COUNT(*) FROM user_levels WHERE user_id = 2") == 1


async def test_delete_user_data_all_guilds(bot, db_pool):
    await fill_user_data(db_pool)
    deleted = await delete_user_data(bot, 1, {'levels', 'voice'})
    assert deleted == 7
    assert await count_rows(db_pool, 'voice_detailed_sessions') == 0
    assert await db_pool.fetchval("SELECT COUNT(*) FROM voice_stats WHERE user_id = 2") == 1


async def test_delete_user_data_clears_active_sessions(bot, db_pool):
    events = SimpleNamespace(active_sessions={1: {}, 2: {}}, pending_sessions={1: {}})
    bot.get_cog.return_value = events
    await delete_user_data(bot, 1, {'voice'}, guild_id=10)
    assert events.active_sessions == {2: {}}
    assert events.pending_sessions == {}


async def test_delete_user_data_levels_only(bot, db_pool):
    await fill_user_data(db_pool)
    deleted = await delete_user_data(bot, 1, {'levels'})
    assert deleted == 2
    assert await count_rows(db_pool, 'voice_detailed_sessions') == 2
