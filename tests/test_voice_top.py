from datetime import date
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from PIL import Image

from cogs.voice_top import (CANVAS_SIZE, MIN_BAR_HEIGHT, TopEntry, VoiceTop, VoiceTopView,
                            NAME_FONT, build_ranking, fit_text, get_bar_height, get_voice_top,
                            render_voice_top)
from factories import (insert_guild_settings, insert_user_settings, insert_voice_stats, make_guild,
                       make_interaction, make_member)


class FakeDate(date):
    """### Дата с фиксированным "сегодня" - 07.11.2026 (суббота)"""
    @classmethod
    def today(cls):
        return cls(2026, 11, 7)


WEEK_START, WEEK_END = date(2026, 11, 2), date(2026, 11, 8)
DAY = date(2026, 11, 3)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """### Фиксированная дата и загрузка аватаров без сети"""
    monkeypatch.setattr('cogs.voice_top.date', FakeDate)
    monkeypatch.setattr(VoiceTopView, 'load_avatars', AsyncMock())


# === Чистые функции ===
@pytest.mark.parametrize('value, page_max, expected', [
    (100, 100, 300),
    (50, 100, 150),
    (1, 100, MIN_BAR_HEIGHT),
    (0, 0, MIN_BAR_HEIGHT),
])
def test_get_bar_height(value, page_max, expected):
    assert get_bar_height(value, page_max) == expected


def test_fit_text():
    font = NAME_FONT
    assert fit_text('Тест', font, 188) == 'Тест'
    long_name = fit_text('Очень длинный никнейм участника сервера', font, 188)
    assert long_name.endswith('...')
    assert font.getlength(long_name) <= 188


def make_entries(count: int) -> list[TopEntry]:
    return [TopEntry(rank, rank, f'Участник {rank}', 3600 * (10 - rank)) for rank in range(1, count + 1)]


@pytest.mark.parametrize('count', [1, 5])
def test_render_voice_top(count):
    entries = make_entries(count)
    entries[0].name = 'Очень длинный никнейм участника сервера'
    # Аватар есть только у первого участника, у остальных - заглушка с буквой
    avatars = {1: Image.new('RGB', (128, 128), 'red')}
    image = Image.open(render_voice_top(entries, avatars))
    assert image.format == 'PNG'
    assert image.size == CANVAS_SIZE


# === Данные ===
async def test_get_voice_top_total(db_pool):
    await insert_voice_stats(db_pool, 1, 10, DAY, 600)
    await insert_voice_stats(db_pool, 1, 10, date(2026, 11, 4), 600)
    await insert_voice_stats(db_pool, 2, 10, DAY, 1000)
    await insert_voice_stats(db_pool, 3, 10, date(2026, 10, 30), 9999)  # прошлая неделя
    await insert_voice_stats(db_pool, 4, 20, DAY, 9999)                 # другой сервер
    assert await get_voice_top(db_pool, 10, WEEK_START, WEEK_END, 'total') == [(1, 1200), (2, 1000)]


async def test_get_voice_top_respects_settings(db_pool):
    for user_id in (1, 2, 3):
        await insert_voice_stats(db_pool, user_id, 10, DAY, 600)
    await insert_user_settings(db_pool, 1, 10, vc_stats_enabled=False)
    await insert_user_settings(db_pool, 2, 10, privacy=False)
    # У пользователя 3 нет записи настроек - он остаётся в топе
    assert await get_voice_top(db_pool, 10, WEEK_START, WEEK_END, 'total') == [(3, 600)]


async def test_get_voice_top_max_sessions_and_ties(db_pool):
    await insert_voice_stats(db_pool, 2, 10, DAY, 3000, max_seconds=900)
    await insert_voice_stats(db_pool, 2, 10, date(2026, 11, 4), 3000, max_seconds=300)
    await insert_voice_stats(db_pool, 1, 10, DAY, 100, max_seconds=900)
    # При равных значениях порядок определяется ID пользователя
    assert await get_voice_top(db_pool, 10, WEEK_START, WEEK_END, 'max_ssn') == [(1, 900), (2, 900)]


def test_build_ranking_skips_missing_and_bots():
    guild = make_guild(10)
    make_member(1, guild=guild, display_name='Первый')
    make_member(3, guild=guild, bot=True)
    make_member(5, guild=guild, display_name='Пятый')
    entries = build_ranking([(1, 500), (2, 400), (3, 300), (5, 200)], guild)
    assert [(e.rank, e.user_id, e.name) for e in entries] == [(1, 1, 'Первый'), (2, 5, 'Пятый')]


# === Команда и кнопки ===
async def fill_top(pool, guild, count: int = 7):
    """### Участники 1..count с убывающим временем "общения" на сервере guild"""
    await insert_guild_settings(pool, guild.id)
    for user_id in range(1, count + 1):
        make_member(user_id, guild=guild, display_name=f'Участник {user_id}')
        await insert_voice_stats(pool, user_id, guild.id, DAY, 1000 * (count + 1 - user_id))


async def run_command(bot, caller):
    cog = VoiceTop(bot)
    interaction = make_interaction(caller)
    await cog.voice_top.callback(cog, interaction)
    return interaction


async def test_voice_top_guild_disabled(bot):
    interaction = await run_command(bot, make_member(1, 10))
    embed = interaction.response.send_message.call_args.kwargs['embed']
    assert 'не разрешён сбор' in embed.description


async def test_voice_top_first_page(bot, db_pool):
    guild = make_guild(10)
    await fill_top(db_pool, guild)
    interaction = await run_command(bot, guild.get_member(6))
    interaction.response.defer.assert_awaited_once()
    kwargs = interaction.edit_original_response.call_args.kwargs
    view: VoiceTopView = kwargs['view']
    assert isinstance(kwargs['attachments'][0], discord.File)
    assert kwargs['embed'].footer.text == 'Страница 1/2 · Ваше место: 6 (33 мин)'
    assert '02.11.2026 — 08.11.2026' in kwargs['embed'].description
    assert view.page_label.label == '1/2'
    assert view.prev_page.disabled and not view.next_page.disabled
    assert view.next_period.disabled


async def test_voice_top_buttons(bot, db_pool):
    guild = make_guild(10)
    await fill_top(db_pool, guild)
    caller = guild.get_member(1)
    view = VoiceTopView(bot, caller, make_interaction(caller))
    await view.show(make_interaction(caller), reload=True)

    await view.next_page.callback(make_interaction(caller))
    assert view.page == 1
    assert view.next_page.disabled and not view.prev_page.disabled

    await view.set_month.callback(make_interaction(caller))
    assert (view.period, view.page) == ('month', 0)
    assert view.set_month.style == discord.ButtonStyle.primary

    await view.prev_period.callback(make_interaction(caller))
    assert view.offset == -1
    assert not view.next_period.disabled
    # В октябре данных нет - топ пустой
    interaction = make_interaction(caller)
    await view.set_max_ssn.callback(interaction)
    assert view.entries == []


async def test_voice_top_empty(bot, db_pool):
    await insert_guild_settings(db_pool, 10)
    interaction = await run_command(bot, make_member(1, 10))
    kwargs = interaction.edit_original_response.call_args.kwargs
    assert kwargs['attachments'] == []
    assert 'нет данных' in kwargs['embed'].description


async def test_voice_top_caller_not_in_top(bot, db_pool):
    guild = make_guild(10)
    await fill_top(db_pool, guild, count=3)
    await insert_user_settings(db_pool, 1, 10, privacy=False)
    interaction = await run_command(bot, guild.get_member(1))
    footer = interaction.edit_original_response.call_args.kwargs['embed'].footer.text
    assert footer == 'Страница 1/1 · Вас нет в топе за этот период'
