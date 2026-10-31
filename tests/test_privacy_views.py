from unittest.mock import MagicMock

from cogs.general import (PRIVACY_CATEGORIES, General, PrivacyConfirmView, PrivacySelect,
                          PrivacySelectView, PrivacyView)
from factories import count_rows, make_interaction, make_member, sent_kwargs


async def fill_levels(pool):
    for guild_id in (10, 20):
        await pool.execute("INSERT INTO user_levels (guild_id, user_id, xp) VALUES ($1, 1, 50)", guild_id)


async def test_privacy_command():
    bot = MagicMock()
    cog = General(bot)
    interaction = make_interaction(make_member(1, 10))
    await cog.privacy.callback(cog, interaction)
    kwargs = sent_kwargs(interaction.response.send_message)
    assert isinstance(kwargs['view'], PrivacyView)
    assert kwargs['ephemeral'] is True


async def test_interaction_check_only_owner():
    view = PrivacyView(MagicMock(), make_interaction(make_member(1, 10)))
    assert await view.interaction_check(make_interaction(make_member(1, 10))) is True
    assert await view.interaction_check(make_interaction(make_member(2, 10))) is False


# === Кнопки PrivacyView ===
async def test_delete_selected_opens_select():
    interaction = make_interaction(make_member(1, 10))
    view = PrivacyView(MagicMock(), interaction)
    await view.delete_selected.callback(interaction)
    new_view = sent_kwargs(interaction.response.edit_message)['view']
    assert isinstance(new_view, PrivacySelectView)
    assert new_view.children[0].guild_id == 10
    assert view.is_finished()


async def test_delete_all_on_guild_asks_confirmation():
    interaction = make_interaction(make_member(1, 10))
    view = PrivacyView(MagicMock(), interaction)
    await view.delete_all_on_guild.callback(interaction)
    confirm = sent_kwargs(interaction.response.edit_message)['view']
    assert isinstance(confirm, PrivacyConfirmView)
    assert confirm.categories == set(PRIVACY_CATEGORIES)
    assert confirm.guild_id == 10


async def test_delete_all_asks_confirmation_for_all_guilds():
    interaction = make_interaction(make_member(1, 10))
    view = PrivacyView(MagicMock(), interaction)
    await view.delete_all.callback(interaction)
    confirm = sent_kwargs(interaction.response.edit_message)['view']
    assert confirm.categories == set(PRIVACY_CATEGORIES)
    assert confirm.guild_id is None


async def test_select_categories_asks_confirmation():
    interaction = make_interaction(make_member(1, 10))
    view = PrivacySelectView(MagicMock(), interaction, guild_id=10)
    select: PrivacySelect = view.children[0]
    select._values = ['levels', 'guess']
    await select.callback(interaction)
    confirm = sent_kwargs(interaction.response.edit_message)['view']
    assert confirm.categories == {'levels', 'guess'}
    assert confirm.guild_id == 10
    assert view.is_finished()


# === Подтверждение удаления ===
async def test_confirm_deletes_data(bot, db_pool):
    await fill_levels(db_pool)
    interaction = make_interaction(make_member(1, 10))
    view = PrivacyConfirmView(bot, interaction, {'levels'}, guild_id=10)
    await view.confirm.callback(interaction)
    # Кнопки убираются до удаления, чтобы нельзя было нажать повторно
    assert sent_kwargs(interaction.response.edit_message)['view'] is None
    embed = interaction.edit_original_response.call_args.kwargs['embed']
    assert embed.title == 'Данные удалены'
    assert 'Удалено записей: **1**' in embed.description
    assert await count_rows(db_pool, 'user_levels') == 1
    assert view.is_finished()


async def test_confirm_db_error():
    bot = MagicMock()
    bot.db_pool = None
    interaction = make_interaction(make_member(1, 10))
    view = PrivacyConfirmView(bot, interaction, {'levels'}, guild_id=10)
    await view.confirm.callback(interaction)
    assert interaction.edit_original_response.call_args.kwargs['embed'].title == 'Ошибка!'


async def test_cancel_keeps_data(bot, db_pool):
    await fill_levels(db_pool)
    interaction = make_interaction(make_member(1, 10))
    view = PrivacyConfirmView(bot, interaction, {'levels'})
    await view.cancel.callback(interaction)
    assert sent_kwargs(interaction.response.edit_message)['embed'].title == 'Удаление отменено'
    assert await count_rows(db_pool, 'user_levels') == 2
