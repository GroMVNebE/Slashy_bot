import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import discord
import numpy as np
import pytest
from PIL import Image

from bot_stats import (StatsView, describe, get_guilds_with_stats, get_stat_counts, record_command_usage,
                       record_message_length, record_session_duration, render_distribution)
from cogs.admin import ManageSettings, ManageView
from cogs.events import Events
from factories import count_rows, insert_guild_settings, make_interaction, make_member, sent_kwargs
from utils import create_default_guild_settings

MSK = ZoneInfo('Europe/Moscow')


@pytest.fixture
async def events_cog(bot):
    """### Ког Events без фоновых задач"""
    cog = Events(bot)
    cog.save_sessions_task.cancel()
    cog.delete_pending_sessions_task.cancel()
    cog.delete_old_sessions_task.cancel()
    return cog


async def test_record_increments_counter(db_pool):
    await record_message_length(db_pool, 10, 25)
    await record_message_length(db_pool, 10, 25)
    await record_message_length(db_pool, 10, 7)
    await record_message_length(db_pool, 20, 25)
    assert await get_stat_counts(db_pool, 'messages', 10) == [(7, 1), (25, 2)]
    assert await get_stat_counts(db_pool, 'messages', 20) == [(25, 1)]


async def test_record_skipped_when_disabled(db_pool):
    await db_pool.execute(
        "INSERT INTO guild_settings (guild_id, only_owner_access, vc_stats_enabled, anon_stats_enabled) "
        "VALUES (10, TRUE, TRUE, FALSE)")
    await record_message_length(db_pool, 10, 25)
    await record_session_duration(db_pool, 10, 30)
    await record_command_usage(db_pool, 10, 'voice')
    for table in ('message_length_stats', 'session_duration_stats', 'command_usage_stats'):
        assert await count_rows(db_pool, table) == 0


async def test_record_with_guild_settings_enabled(db_pool):
    # Запись настроек без явного значения - сбор включён по умолчанию
    await insert_guild_settings(db_pool, 10)
    await record_session_duration(db_pool, 10, 30)
    assert await get_stat_counts(db_pool, 'sessions', 10) == [(30, 1)]


async def test_record_db_error_is_not_raised():
    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=RuntimeError('БД недоступна'))
    await record_command_usage(pool, 10, 'voice')


async def test_default_guild_settings_enable_anon_stats(db_pool):
    guild = MagicMock()
    guild.id = 10
    await create_default_guild_settings(db_pool, guild)
    assert await db_pool.fetchval("SELECT anon_stats_enabled FROM guild_settings WHERE guild_id = 10") is True


def make_message(author, content: str) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.author = author
    message.content = content
    return message


async def test_on_message_records_length(events_cog, db_pool):
    await events_cog.on_message(make_message(make_member(1, 10), 'Привет всем!'))
    # только вложение
    await events_cog.on_message(make_message(make_member(1, 10), ''))
    assert await get_stat_counts(db_pool, 'messages', 10) == [(12, 1)]


async def test_save_time_records_minutes(events_cog, db_pool):
    left_at = time.time()
    session = {'prd_start': left_at - 70 * 60 - 30,
               'session_start': datetime.fromtimestamp(left_at - 70 * 60 - 30, MSK),
               'guild_id': 10, 'left_at': left_at}
    await events_cog.save_time(1, session, autosave=False)
    # автосохранение не учитывается
    await events_cog.save_time(2, dict(session), autosave=True)
    assert await get_stat_counts(db_pool, 'sessions', 10) == [(70, 1)]


async def test_command_completion_records_usage(events_cog, db_pool):
    command = MagicMock()
    command.qualified_name = 'voice-top'
    await events_cog.on_app_command_completion(make_interaction(make_member(1, 10)), command)
    dm_interaction = make_interaction(make_member(1, 10))
    dm_interaction.guild_id = None
    await events_cog.on_app_command_completion(dm_interaction, command)
    assert await get_stat_counts(db_pool, 'commands', None) == [('voice-top', 1)]


async def test_get_stat_counts_all_guilds_and_order(db_pool):
    for guild_id, command, times in ((10, 'voice', 3), (20, 'voice', 2), (10, 'lvl', 5), (20, 'guess', 5)):
        for _ in range(times):
            await record_command_usage(db_pool, guild_id, command)
    assert await get_stat_counts(db_pool, 'commands', None) == [('guess', 5), ('lvl', 5), ('voice', 5)]
    assert await get_stat_counts(db_pool, 'commands', 10) == [('lvl', 5), ('voice', 3)]


async def test_get_guilds_with_stats(db_pool):
    await record_message_length(db_pool, 10, 5)
    await record_command_usage(db_pool, 20, 'voice')
    await record_command_usage(db_pool, 20, 'voice')
    await record_session_duration(db_pool, 30, 15)
    assert await get_guilds_with_stats(db_pool) == [(20, 2), (10, 1), (30, 1)]


def test_describe_known_sample():
    stats = describe([1, 2, 3], [1, 1, 2])  # выборка 1, 2, 3, 3
    assert stats['n'] == 4
    assert stats['mean'] == pytest.approx(2.25)
    assert stats['median'] == pytest.approx(2.5)
    assert stats['p99'] == pytest.approx(np.percentile([1, 2, 3, 3], 99))


def test_describe_matches_numpy():
    values, counts = [5, 10, 40, 200], [30, 12, 4, 1]
    sample = np.repeat(values, counts)
    stats = describe(values, counts)
    assert stats['std'] == pytest.approx(sample.std())
    for p in (25, 75, 90):
        assert stats[f'p{p}'] == pytest.approx(np.percentile(sample, p))


def test_describe_single_value():
    stats = describe([42], [3])
    assert (stats['mean'], stats['median'], stats['std']) == (42, 42, 0)


@pytest.mark.parametrize('values, counts', [
    ([5, 10, 40], [30, 12, 4]),
    ([7], [10]),
    (list(range(1, 101)) + [5000], [3] * 100 + [1]),  # с выбросом
    ([0, 1], [5, 2]),                                   # сессии короче минуты
])
def test_render_distribution(values, counts):
    image = Image.open(render_distribution(
        values, counts, 'Длина сообщения (символы)', '#5865F2'))
    assert image.format == 'PNG'


OWNER_ID = 1000


async def open_stats(bot):
    interaction = make_interaction(make_member(OWNER_ID, 10))
    view = await StatsView.create(bot, interaction)
    await view.show(interaction)
    return view, interaction


async def test_stats_view_messages_tab(bot, db_pool):
    await record_message_length(db_pool, 10, 12)
    view, interaction = await open_stats(bot)
    kwargs = interaction.edit_original_response.call_args.kwargs
    assert '**Кол-во:** 1' in kwargs['embed'].description
    assert isinstance(kwargs['attachments'][0], discord.File)
    assert view.prev_page not in view.children


async def test_stats_view_access_only_owner(bot):
    view, _ = await open_stats(bot)
    assert await view.interaction_check(make_interaction(make_member(OWNER_ID, 10))) is True
    assert await view.interaction_check(make_interaction(make_member(1, 10))) is False


async def test_stats_view_empty(bot):
    _, interaction = await open_stats(bot)
    kwargs = interaction.edit_original_response.call_args.kwargs
    assert kwargs['embed'].description == 'Данных пока нет'
    assert kwargs['attachments'] == []


async def test_stats_view_commands_paging(bot, db_pool):
    for idx in range(23):
        for _ in range(idx + 1):
            await record_command_usage(db_pool, 10, f'cmd{idx:02d}')
    view, _ = await open_stats(bot)
    interaction = make_interaction(make_member(OWNER_ID, 10))
    await view.tab_commands.callback(interaction)
    kwargs = interaction.edit_original_response.call_args.kwargs
    assert kwargs['embed'].description.startswith('1. `/cmd22` — 23')
    assert kwargs['embed'].footer.text == 'Страница 1/3 · всего вызовов: 276'
    assert view.prev_page in view.children and view.prev_page.disabled

    await view.next_page.callback(make_interaction(make_member(OWNER_ID, 10)))
    await view.next_page.callback(make_interaction(make_member(OWNER_ID, 10)))
    assert view.page == 2 and view.next_page.disabled
    assert view.page_label.label == '3/3'

    await view.tab_sessions.callback(make_interaction(make_member(OWNER_ID, 10)))
    assert view.page == 0
    assert view.prev_page not in view.children


async def test_stats_view_guild_select(bot, db_pool):
    await record_command_usage(db_pool, 10, 'voice')
    await record_command_usage(db_pool, 20, 'lvl')
    view, _ = await open_stats(bot)
    assert [option.value for option in view.guild_select.options] == [
        'all', '10', '20']
    view.tab, view.page = 'commands', 1
    view.guild_select._values = ['20']
    interaction = make_interaction(make_member(OWNER_ID, 10))
    await view.guild_select.callback(interaction)
    assert (view.guild_id, view.page) == (20, 0)
    assert '`/lvl`' in interaction.edit_original_response.call_args.kwargs['embed'].description


async def test_manage_opens_stats(bot):
    interaction = make_interaction(make_member(OWNER_ID, 10))
    manage_view = ManageView(bot, interaction)
    action_select = manage_view.children[0]
    action_select._values = ['stats']
    await action_select.callback(interaction)
    assert isinstance(
        interaction.edit_original_response.call_args.kwargs['view'], StatsView)
    assert manage_view.is_finished()


async def test_setup_shows_anon_stats_setting(bot, db_pool):
    interaction = make_interaction(make_member(1, 10))
    await ManageSettings(bot, 'server').draw_page(interaction)
    description = sent_kwargs(interaction.response.edit_message)[
        'embed'].description
    assert '**Сбор анонимной статистики (длина сообщений, сессии, команды):** ***Включен***' in description
