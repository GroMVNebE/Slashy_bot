from datetime import date
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.general import General
from factories import (insert_guild_settings, insert_user_settings, insert_voice_stats,
                       make_interaction, make_member)
from utils import readable_time


class FakeDate(date):
    """### Дата с фиксированным "сегодня" - 15.10.2026 (четверг)"""
    @classmethod
    def today(cls):
        return cls(2026, 10, 15)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr('cogs.general.date', FakeDate)


def stats_view(bot, user=None):
    user = user or make_member(1, 10)
    return General.VoiceStatsView(bot, user, make_interaction(user))


async def fill_week(pool):
    """### Время "общения" пользователя 1 на сервере 10 и посторонние записи"""
    await insert_voice_stats(pool, 1, 10, date(2026, 10, 13), 3600, max_seconds=600)
    await insert_voice_stats(pool, 1, 10, date(2026, 10, 18), 7200, max_seconds=900)
    await insert_voice_stats(pool, 1, 10, date(2026, 10, 19), 500)   # следующая неделя
    await insert_voice_stats(pool, 1, 10, date(2026, 1, 5), 1000)    # январь
    await insert_voice_stats(pool, 2, 10, date(2026, 10, 13), 9999)  # другой пользователь
    await insert_voice_stats(pool, 1, 20, date(2026, 10, 13), 9999)  # другой сервер


# === Команда /voice: проверки доступа ===
async def call_voice(bot, interaction, member=None):
    cog = General(bot)
    await cog.voice.callback(cog, interaction, member)


async def test_voice_guild_disabled(bot):
    interaction = make_interaction(make_member(1, 10))
    await call_voice(bot, interaction)
    embed = interaction.response.send_message.call_args.kwargs['embed']
    assert 'не разрешён сбор' in embed.description


async def test_voice_target_opted_out(bot, db_pool):
    await insert_guild_settings(db_pool, 10)
    await insert_user_settings(db_pool, 2, 10, vc_stats_enabled=False)
    interaction = make_interaction(make_member(1, 10))
    await call_voice(bot, interaction, make_member(2, 10))
    embed = interaction.response.send_message.call_args.kwargs['embed']
    assert 'запретил сбор' in embed.description


async def test_voice_other_user_private(bot, db_pool):
    await insert_guild_settings(db_pool, 10)
    await insert_user_settings(db_pool, 2, 10, privacy=False)
    interaction = make_interaction(make_member(1, 10))
    await call_voice(bot, interaction, make_member(2, 10))
    embed = interaction.response.send_message.call_args.kwargs['embed']
    assert 'запретил просмотр' in embed.description
    interaction.response.defer.assert_not_awaited()


async def test_voice_own_private_stats_allowed(bot, db_pool):
    await insert_guild_settings(db_pool, 10)
    await insert_user_settings(db_pool, 1, 10, privacy=False)
    interaction = make_interaction(make_member(1, 10))
    await call_voice(bot, interaction)
    interaction.response.send_message.assert_not_awaited()
    kwargs = interaction.edit_original_response.call_args.kwargs
    assert isinstance(kwargs['view'], General.VoiceStatsView)
    assert isinstance(kwargs['attachments'][0], discord.File)
    assert 'Тестер' in kwargs['embed'].title


async def test_voice_creates_target_settings(bot, db_pool):
    await insert_guild_settings(db_pool, 10)
    interaction = make_interaction(make_member(1, 10))
    await call_voice(bot, interaction, make_member(2, 10, display_name='Другой'))
    row = await db_pool.fetchrow("SELECT vc_stats_enabled FROM user_settings WHERE user_id = 2")
    assert row['vc_stats_enabled'] is True
    assert 'Другой' in interaction.edit_original_response.call_args.kwargs['embed'].title


async def test_voice_db_error():
    bot = MagicMock()
    bot.db_pool.acquire.side_effect = RuntimeError('БД недоступна')
    interaction = make_interaction(make_member(1, 10))
    await call_voice(bot, interaction)
    embed = interaction.response.send_message.call_args.kwargs['embed']
    assert embed.title == 'Произошла ошибка'
    # После ошибки команда завершается и не пытается построить график
    interaction.response.defer.assert_not_awaited()


# === get_stats_data ===
async def test_stats_week(bot, db_pool):
    await fill_week(db_pool)
    labels, values, range_text = await stats_view(bot).get_stats_data()
    assert labels == ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    assert values == [0, 3600, 0, 0, 0, 0, 7200]
    assert range_text == '12.10.2026 — 18.10.2026'


async def test_stats_previous_week(bot, db_pool):
    view = stats_view(bot)
    view.offset = -1
    _, _, range_text = await view.get_stats_data()
    assert range_text == '05.10.2026 — 11.10.2026'


async def test_stats_month(bot, db_pool):
    await fill_week(db_pool)
    view = stats_view(bot)
    view.current_period = 'month'
    labels, values, range_text = await view.get_stats_data()
    assert range_text == '01.10.2026 — 31.10.2026'
    assert len(values) == 31
    assert values[12] == 3600 and values[17] == 7200 and values[18] == 500
    assert [labels[i] for i in (0, 1, 4, 30)] == ['1', '', '5', '31']


@pytest.mark.parametrize('offset, expected', [
    (-10, '01.12.2025 — 31.12.2025'),
    (-22, '01.12.2024 — 31.12.2024'),
    (-9, '01.01.2026 — 31.01.2026'),
])
async def test_stats_month_offset(bot, db_pool, offset, expected):
    view = stats_view(bot)
    view.current_period = 'month'
    view.offset = offset
    _, values, range_text = await view.get_stats_data()
    assert range_text == expected
    assert len(values) == 31


async def test_stats_year_total(bot, db_pool):
    await fill_week(db_pool)
    view = stats_view(bot)
    view.current_period = 'year'
    labels, values, range_text = await view.get_stats_data()
    assert range_text == '01.01.2026 — 31.12.2026'
    assert labels[0] == 'Янв' and len(values) == 12
    assert values[0] == 1000
    assert values[9] == 3600 + 7200 + 500


async def test_stats_year_max_sessions(bot, db_pool):
    await fill_week(db_pool)
    view = stats_view(bot)
    view.current_period = 'year'
    view.mode = 'max_ssn'
    _, values, _ = await view.get_stats_data()
    assert values[9] == 900
    assert values[0] == 0


# === Кнопки ===
async def test_stats_buttons():
    view = stats_view(MagicMock())
    view.update_stats = AsyncMock()
    interaction = make_interaction(view.user)
    assert view.next_period.disabled is True

    await view.next_period.callback(interaction)
    assert view.offset == 0
    view.update_stats.assert_not_awaited()

    await view.prev_period.callback(interaction)
    assert view.offset == -1
    assert view.next_period.disabled is False

    await view.set_month.callback(interaction)
    assert (view.current_period, view.offset) == ('month', 0)
    assert view.set_month.style == discord.ButtonStyle.primary
    assert view.set_week.style == discord.ButtonStyle.secondary

    await view.set_max_ssn.callback(interaction)
    assert view.mode == 'max_ssn'
    assert view.set_max_ssn.style == discord.ButtonStyle.primary
    assert view.set_total.style == discord.ButtonStyle.secondary
    assert view.update_stats.await_count == 3


# === update_stats ===
@pytest.mark.parametrize('mode, expected', [
    ('total', f'Время "общения"**: `{readable_time(10800)}`'),
    ('max_ssn', f'Максимальная сессия "общения"**: `{readable_time(900)}`'),
])
async def test_update_stats_description(bot, db_pool, mode, expected):
    await fill_week(db_pool)
    view = stats_view(bot)
    view.mode = mode
    interaction = make_interaction(view.user)
    await view.update_stats(interaction)
    interaction.response.defer.assert_awaited_once()
    assert expected in interaction.edit_original_response.call_args.kwargs['embed'].description
