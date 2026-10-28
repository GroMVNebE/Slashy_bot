import pytest

from cogs.general import PRIVACY_CATEGORIES, _affected, _confirm_embed


# === _affected ===
@pytest.mark.parametrize('status, expected', [
    ('DELETE 3', 3),
    ('UPDATE 0', 0),
])
def test_affected(status, expected):
    assert _affected(status) == expected


# === _confirm_embed ===
def test_confirm_embed_all_guilds():
    embed = _confirm_embed({'levels'})
    assert 'на всех серверах' in embed.description
    assert 'необратимо' in embed.description


def test_confirm_embed_current_guild():
    embed = _confirm_embed({'levels'}, guild_id=1)
    assert 'на текущем сервере' in embed.description


def test_confirm_embed_voice_includes_detailed():
    embed = _confirm_embed({'voice'})
    assert PRIVACY_CATEGORIES['voice']['label'] in embed.description
    assert PRIVACY_CATEGORIES['detailed']['label'] in embed.description
