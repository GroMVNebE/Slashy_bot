import io

import discord
import pytest
from PIL import Image

from cogs.general import General
from factories import count_rows, make_interaction, make_member
from snapshots import assert_image_matches


def make_avatar_bytes() -> bytes:
    """### Детерминированный аватар 128x128: четыре цветных квадрата"""
    image = Image.new('RGB', (128, 128))
    for (x, y), color in (((0, 0), '#ED4245'), ((64, 0), '#FAA61A'), ((0, 64), '#3BA55D'), ((64, 64), '#5865F2')):
        image.paste(color, (x, y, x + 64, y + 64))
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status: int):
        self.status = status

    async def read(self):
        return make_avatar_bytes()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    """### Заглушка aiohttp.ClientSession: вместо загрузки аватара по сети отдаёт make_avatar_bytes()"""
    status = 200

    def get(self, url):
        return FakeResponse(self.status)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.fixture(autouse=True)
def fake_avatar(monkeypatch):
    FakeSession.status = 200
    monkeypatch.setattr('cogs.general.aiohttp.ClientSession', FakeSession)


async def call_lvl(bot, caller, member=None):
    cog = General(bot)
    interaction = make_interaction(caller)
    await cog.lvl.callback(cog, interaction, member)
    return interaction


async def set_level(pool, user_id: int, level: int, xp: int):
    await pool.execute("INSERT INTO user_levels (guild_id, user_id, xp, level) VALUES (10, $1, $2, $3)",
                       user_id, xp, level)


@pytest.mark.parametrize('name, snapshot', [
    ('Тестер Снимков', 'lvl_card'),
    ('Очень длинный никнейм для проверки обрезки', 'lvl_card_long_name'),
])
async def test_lvl_card_snapshot(bot, db_pool, name, snapshot):
    await set_level(db_pool, 1, 7, 1234)
    interaction = await call_lvl(bot, make_member(1, 10, display_name=name))
    kwargs = interaction.followup.send.call_args.kwargs
    assert kwargs['embed'].title == f'Данные об уровне {name}'
    card: discord.File = kwargs['file']
    assert card.filename == 'lvl_card_1.png'
    assert_image_matches(card.fp.getvalue(), snapshot)


async def test_lvl_card_for_other_member(bot, db_pool):
    await set_level(db_pool, 2, 3, 50)
    caller = make_member(1, 10)
    interaction = await call_lvl(bot, caller, make_member(2, 10, display_name='Другой'))
    kwargs = interaction.followup.send.call_args.kwargs
    assert kwargs['embed'].title == 'Данные об уровне Другой'
    assert kwargs['file'].filename == 'lvl_card_2.png'


async def test_lvl_new_user_not_saved(bot, db_pool):
    interaction = await call_lvl(bot, make_member(1, 10))
    assert interaction.followup.send.call_args.kwargs['file'].filename == 'lvl_card_1.png'
    # Просмотр уровня ничего не записывает в БД
    assert await count_rows(db_pool, 'user_levels') == 0


async def test_lvl_bot_target(bot, db_pool):
    interaction = await call_lvl(bot, make_member(1, 10), make_member(3, 10, bot=True))
    embed = interaction.response.send_message.call_args.kwargs['embed']
    assert 'нельзя применять к ботам' in embed.description
    interaction.response.defer.assert_not_awaited()


async def test_lvl_avatar_download_failed(bot, db_pool):
    FakeSession.status = 404
    interaction = await call_lvl(bot, make_member(1, 10))
    kwargs = interaction.followup.send.call_args.kwargs
    assert kwargs['embed'].title == 'Ошибка!'
    assert 'file' not in kwargs
