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
def test_get_required_xp_first_level():
    assert get_required_xp(1) == 150


def test_get_required_xp_rounded_and_increasing():
    values = [get_required_xp(level) for level in range(1, 101)]
    assert all(v % 10 == 0 for v in values)
    assert all(a < b for a, b in zip(values, values[1:]))


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
ENV_KEY = 'SLASHY_TEST_VARIABLE'


def test_get_env_value(monkeypatch):
    monkeypatch.setenv(ENV_KEY, 'value')
    assert get_env(ENV_KEY) == 'value'


def test_get_env_default(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    assert get_env(ENV_KEY, 'default') == 'default'
    monkeypatch.setenv(ENV_KEY, '')
    assert get_env(ENV_KEY, 'default') == 'default'


def test_get_env_missing(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    with pytest.raises(ValueError):
        get_env(ENV_KEY)


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
