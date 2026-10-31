from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.admin import (Admin, ChangeSetting, ManageSettings, ServerSettingsButton, SetupView,
                        UserSettingsButton, user_settings)
from factories import (insert_guild_settings, insert_user_settings, make_guild, make_interaction,
                       make_member, sent_kwargs)


def has_server_button(view: SetupView) -> bool:
    return any(isinstance(item, ServerSettingsButton) for item in view.children)


# === Права доступа: SetupView.create ===
async def test_setup_owner_gets_server_settings(bot):
    guild = make_guild(10, owner_id=1)
    view = await SetupView.create(bot, make_interaction(make_member(1, guild=guild)))
    assert has_server_button(view)
    assert any(isinstance(item, UserSettingsButton) for item in view.children)


@pytest.mark.parametrize('permission', ['administrator', 'manage_guild'])
async def test_setup_admin_without_guild_settings(bot, permission):
    # Без записи настроек сервера действует значение по умолчанию - только владелец
    member = make_member(1, 10, **{permission: True})
    assert not has_server_button(await SetupView.create(bot, make_interaction(member)))


@pytest.mark.parametrize('only_owner, expected', [(False, True), (True, False)])
async def test_setup_admin_only_owner_access(bot, db_pool, only_owner, expected):
    await db_pool.execute(
        "INSERT INTO guild_settings (guild_id, only_owner_access, vc_stats_enabled) VALUES (10, $1, FALSE)",
        only_owner)
    member = make_member(1, 10, administrator=True)
    assert has_server_button(await SetupView.create(bot, make_interaction(member))) is expected


async def test_setup_regular_member(bot, db_pool):
    await db_pool.execute(
        "INSERT INTO guild_settings (guild_id, only_owner_access, vc_stats_enabled) VALUES (10, FALSE, FALSE)")
    assert not has_server_button(await SetupView.create(bot, make_interaction(make_member(1, 10))))


async def test_setup_command(bot):
    cog = Admin(bot)
    interaction = make_interaction(make_member(1, 10))
    await cog.setup.callback(cog, interaction)
    interaction.response.defer.assert_awaited_once()
    assert isinstance(interaction.edit_original_response.call_args.kwargs['view'], SetupView)


# === Отображение настроек: ManageSettings.draw_page ===
async def test_draw_user_settings_creates_defaults(bot, db_pool):
    interaction = make_interaction(make_member(1, 10))
    view = ManageSettings(bot, 'user')
    await view.draw_page(interaction)
    description = sent_kwargs(interaction.response.edit_message)['embed'].description
    # Сбор на сервере не настроен - по умолчанию сбор у пользователя выключен
    assert '**Сбор статистики времени "общения":** ***Отключен***' in description
    assert '**Доступ к статистике времени "общения":** ***Всем***' in description
    assert await db_pool.fetchval("SELECT COUNT(*) FROM user_settings WHERE user_id = 1") == 1


async def test_draw_server_settings_creates_defaults(bot, db_pool):
    interaction = make_interaction(make_member(1, 10))
    view = ManageSettings(bot, 'server')
    await view.draw_page(interaction)
    description = sent_kwargs(interaction.response.edit_message)['embed'].description
    assert '***Только владельцу***' in description
    assert await db_pool.fetchval("SELECT COUNT(*) FROM guild_settings WHERE guild_id = 10") == 1


# === Изменение значения: SettingChoiceButton ===
async def choose(bot, category: str, code: str, value, user_id: int = 1):
    """### Открывает настройки, выбирает настройку code и нажимает кнопку со значением value"""
    manage_view = ManageSettings(bot, category)
    first = make_interaction(make_member(user_id, 10))
    await manage_view.draw_page(first)
    setting = next(s for s in manage_view.settings if s['code'] == code)
    second = make_interaction(make_member(user_id, 10))
    change_view = ChangeSetting(bot, setting, category, manage_view, second)
    button = next(b for b in change_view.children if b.choice_value == value)
    await button.callback(second)
    return first, second


async def test_change_user_setting_only_own_row(bot, db_pool):
    await insert_user_settings(db_pool, 2, 10, detailed=False)
    first, second = await choose(bot, 'user', 'vc_detailed_stats_enabled', True)
    assert await db_pool.fetchval(
        "SELECT vc_detailed_stats_enabled FROM user_settings WHERE user_id = 1") is True
    assert await db_pool.fetchval(
        "SELECT vc_detailed_stats_enabled FROM user_settings WHERE user_id = 2") is False
    assert 'Включен' in sent_kwargs(second.response.edit_message)['embed'].description
    # Страница с настройками перерисована с новым значением
    first.edit_original_response.assert_awaited_once()


async def test_change_server_setting(bot, db_pool):
    await insert_guild_settings(db_pool, 20, vc_stats_enabled=False)
    await choose(bot, 'server', 'vc_stats_enabled', True)
    assert await db_pool.fetchval("SELECT vc_stats_enabled FROM guild_settings WHERE guild_id = 10") is True
    assert await db_pool.fetchval("SELECT vc_stats_enabled FROM guild_settings WHERE guild_id = 20") is False


# === Страницы ===
def test_pages_split(monkeypatch):
    monkeypatch.setattr('cogs.admin.user_settings', [user_settings[0]] * 6)
    view = ManageSettings(MagicMock(), 'user')
    assert [len(page) for page in view.pages] == [5, 1]


async def test_pages_navigation(monkeypatch):
    monkeypatch.setattr('cogs.admin.user_settings', [user_settings[0]] * 6)
    view = ManageSettings(MagicMock(), 'user')
    view.draw_page = AsyncMock()
    interaction = make_interaction(make_member())
    await view.prev_btn.callback(interaction)
    assert view.cur_page == 1
    await view.next_btn.callback(interaction)
    assert view.cur_page == 0
    await view.next_btn.callback(interaction)
    assert view.cur_page == 1
