from unittest.mock import MagicMock

from cogs.admin import CREATOR_ID, Admin, ManageView
from factories import make_interaction, make_member, sent_kwargs


async def test_manage_denied_for_other_users():
    cog = Admin(MagicMock())
    interaction = make_interaction(make_member(CREATOR_ID + 1))
    await cog.manage.callback(cog, interaction)
    kwargs = sent_kwargs(interaction.response.send_message)
    assert kwargs['embed'].title == 'У Вас нет доступа к этой команде'
    assert 'view' not in kwargs


async def test_manage_opens_panel_for_creator():
    cog = Admin(MagicMock())
    interaction = make_interaction(make_member(CREATOR_ID))
    await cog.manage.callback(cog, interaction)
    kwargs = sent_kwargs(interaction.response.send_message)
    assert isinstance(kwargs['view'], ManageView)
    assert kwargs['ephemeral'] is True
