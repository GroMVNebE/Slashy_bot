import os
from unittest import mock

import discord
import pytest

from utils import (create_embed, get_env, get_info, get_plural, get_required_xp,
                   get_voice_hashes, hash_data, make_log_xp, readable_time)
from cogs.events import _message_curve, _session_curve

FORMS = ('единица', 'единицы', 'единиц')


# === get_plural ===
@pytest.mark.parametrize('val', [1, 21, 101, -1])
def test_get_plural_first_form(val):
    assert get_plural(val, FORMS) == 'единица'


@pytest.mark.parametrize('val', [2, 4, 22, -3])
def test_get_plural_second_form(val):
    assert get_plural(val, FORMS) == 'единицы'


@pytest.mark.parametrize('val', [0, 5, 11, 12, 13, 14, 25, 111])
def test_get_plural_third_form(val):
    assert get_plural(val, FORMS) == 'единиц'


# === readable_time ===
@pytest.mark.parametrize('seconds', [0, -5])
def test_readable_time_invalid(seconds):
    assert readable_time(seconds) == 'Время не указано'


@pytest.mark.parametrize('seconds, expected', [
    (59, '59 секунд'),
    (3661, '1 час 1 минута 1 секунда'),
    (7200, '2 часа'),
    (125, '2 минуты 5 секунд'),
])
def test_readable_time(seconds, expected):
    assert readable_time(seconds) == expected


# === get_required_xp ===
def test_get_required_xp_valid_attributes():
    values = [get_required_xp(level) for level in range(1, 101)]
    assert all(val % 10 == 0 for val in values)
    assert all(values[i] < values[i+1] for i in range(len(values)-1))


@pytest.mark.parametrize('level, value', [
    pytest.param(1, 150, id='start-level'),
    pytest.param(5, 1180),
    pytest.param(10, 3080),
    pytest.param(15, 5500),
    pytest.param(25, 11640),
    pytest.param(50, 33050),
    pytest.param(75, 61560),
    pytest.param(100, 96100),
])
def test_get_required_xp_known_values(level, value):
    assert get_required_xp(level) == value


@pytest.mark.parametrize('level, match', [
    pytest.param(0, '0 <= 0',
                 id='zero-level'),
    pytest.param(-2, '-2 <= 0',
                 id='negative-level'),
])
def test_get_required_xp_invalid(level, match):
    with pytest.raises(ValueError, match=match):
        get_required_xp(level)


# === make_log_xp ===
def test_make_log_xp_reference_points():
    curve = make_log_xp(threshold=10, base_xp=10,
                        point=70, point_xp=30, cap=480)
    assert curve(9) == 0
    assert curve(10) == 10
    assert curve(70) == 30


def test_make_log_xp_cap_and_monotonic():
    curve = make_log_xp(threshold=10, base_xp=10,
                        point=70, point_xp=30, cap=480)
    values = [curve(x) for x in range(10, 1000)]
    assert all(a <= b for a, b in zip(values, values[1:]))
    assert curve(480) == curve(10_000)


def test_make_log_xp_minimum_is_one():
    curve = make_log_xp(threshold=5, base_xp=0, point=10, point_xp=10, cap=100)
    assert curve(5) == 1


def test_message_curve():
    assert _message_curve(4) == 0
    assert _message_curve(5) == 5
    assert _message_curve(10) == 10


def test_session_curve():
    assert _session_curve(9) == 0
    assert _session_curve(10) == 10
    assert _session_curve(70) == 30


# === hash_data ===
DATA = [{'type': 'id', 'data': '123'}]


def test_hash_data_deterministic_hex():
    result = hash_data(DATA, 'log')
    assert result == hash_data(DATA, 'log')
    assert len(result) == 64
    int(result, 16)


def test_hash_data_category_changes_hash():
    assert hash_data(DATA, 'log') != hash_data(DATA, 'voice')


def test_hash_data_unknown_category():
    with pytest.raises(ValueError):
        hash_data(DATA, 'unknown')


# === get_info ===
def test_get_info_none():
    assert get_info(None) == 'Нет данных'


def test_get_info_int_equals_object():
    assert get_info(123456789) == get_info(discord.Object(id=123456789))


def test_get_info_hides_id():
    assert '123456789' not in get_info(123456789)


# === get_voice_hashes ===
def test_get_voice_hashes_user_depends_on_guild():
    uid_1, _ = get_voice_hashes(1, 10)
    uid_2, _ = get_voice_hashes(1, 20)
    assert uid_1 != uid_2


def test_get_voice_hashes_guild_same_for_users():
    _, gid_1 = get_voice_hashes(1, 10)
    _, gid_2 = get_voice_hashes(2, 10)
    assert gid_1 == gid_2


def test_get_voice_hashes_matches_hash_data():
    uid, gid = get_voice_hashes(1, 10)
    assert uid == hash_data([{'type': 'uid', 'data': '1'},
                             {'type': 'gid', 'data': '10'}], 'voice')
    assert gid == hash_data([{'type': 'gid', 'data': '10'}], 'voice')


# === get_env ===
ENV = {'VALID': 'valid', 'BLANK': '', 'LONG_BLANK': '   ', 'SPACES': '  123  '}


@mock.patch.dict(os.environ, ENV, clear=True)
@pytest.mark.parametrize('val, default, expected', [
    pytest.param('VALID', None, 'valid',
                 id='existing-value-without-default'),
    pytest.param('VALID', 'invalid', 'valid',
                 id='existing-value-with-default'),
    pytest.param('INVALID', 'valid', 'valid',
                 id='nonexisting-value-with-default'),
    pytest.param('BLANK', 'default', 'default', id='blank-value-with-default'),
    pytest.param('LONG_BLANK', 'default', 'default',
                 id='long-blank-value-with-default'),
    pytest.param('SPACES', None, '123',
                 id='value-with-spaces'),
])
def test_get_env_valid(val, default, expected):
    assert get_env(val, default) == expected


@mock.patch.dict(os.environ, ENV, clear=True)
@pytest.mark.parametrize('val, match', [
    pytest.param('INVALID', 'INVALID не задана',
                 id='nonexisting-value-without-default'),
    pytest.param('BLANK', 'BLANK не задана',
                 id='blank-value-without-default'),
    pytest.param('LONG_BLANK',
                 'LONG_BLANK не задана', id='long-blank-value-without-default')
])
def test_get_env_invalid(val, match):
    with pytest.raises(ValueError, match=match):
        get_env(val)


# === create_embed ===
def test_create_embed_basic():
    embed = create_embed(title='Заголовок', description='Описание',
                         color=discord.Color.red())
    assert embed.title == 'Заголовок'
    assert embed.description == 'Описание'
    assert embed.color == discord.Color.red()
    assert embed.timestamp is not None


def test_create_embed_fields_and_no_timestamp():
    embed = create_embed(fields=[{'name': 'Поле', 'value': 'Значение'},
                                 {'name': 'Поле 2', 'value': 'Значение 2', 'inline': False}],
                         timestamp=False)
    assert embed.timestamp is None
    assert [(f.name, f.value, f.inline) for f in embed.fields] == [
        ('Поле', 'Значение', True), ('Поле 2', 'Значение 2', False)]
