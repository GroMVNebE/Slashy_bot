import pytest

from factories import insert_guild_settings, make_guild, make_member
from utils import add_xp, create_default_guild_settings, create_default_user_settings, get_required_xp


async def get_level(pool, user_id=1, guild_id=10):
    return await pool.fetchrow(
        "SELECT xp, level FROM user_levels WHERE guild_id = $1 AND user_id = $2", guild_id, user_id)


# === add_xp ===
async def test_add_xp_new_user(db_pool):
    await add_xp(user_id=1, guild_id=10, xp=40, pool=db_pool)
    row = await get_level(db_pool)
    assert (row['xp'], row['level']) == (40, 1)


async def test_add_xp_accumulates(db_pool):
    await add_xp(1, 10, 40, db_pool)
    await add_xp(1, 10, 30, db_pool)
    row = await get_level(db_pool)
    assert (row['xp'], row['level']) == (70, 1)


async def test_add_xp_level_up_with_remainder(db_pool):
    await add_xp(1, 10, get_required_xp(1) + 5, db_pool)
    row = await get_level(db_pool)
    assert (row['xp'], row['level']) == (5, 2)


async def test_add_xp_several_levels(db_pool):
    await add_xp(1, 10, get_required_xp(1) + get_required_xp(2) + 10, db_pool)
    row = await get_level(db_pool)
    assert (row['xp'], row['level']) == (10, 3)


async def test_add_xp_separate_guilds(db_pool):
    await add_xp(1, 10, 40, db_pool)
    await add_xp(1, 20, 50, db_pool)
    assert (await get_level(db_pool, guild_id=10))['xp'] == 40
    assert (await get_level(db_pool, guild_id=20))['xp'] == 50


@pytest.mark.parametrize('xp', [0, -10])
async def test_add_xp_not_positive(db_pool, xp):
    with pytest.raises(ValueError):
        await add_xp(1, 10, xp, db_pool)


# === Настройки по умолчанию ===
async def test_create_default_guild_settings(db_pool):
    await create_default_guild_settings(db_pool, make_guild(10))
    row = await db_pool.fetchrow("SELECT * FROM guild_settings WHERE guild_id = 10")
    assert row['only_owner_access'] is True
    assert row['vc_stats_enabled'] is False


async def test_create_default_user_settings_without_guild_settings(db_pool):
    await create_default_user_settings(db_pool, make_member(1, 10))
    row = await db_pool.fetchrow("SELECT * FROM user_settings WHERE guild_id = 10 AND user_id = 1")
    assert row['vc_stats_enabled'] is False
    assert row['vc_stats_privacy'] is True
    assert row['vc_detailed_stats_enabled'] is False


async def test_create_default_user_settings_guild_disabled(db_pool):
    # Запись настроек сервера есть, но сбор выключен - у пользователя сбор тоже выключен
    await insert_guild_settings(db_pool, 10, vc_stats_enabled=False)
    await create_default_user_settings(db_pool, make_member(1, 10))
    row = await db_pool.fetchrow("SELECT * FROM user_settings WHERE guild_id = 10 AND user_id = 1")
    assert row['vc_stats_enabled'] is False


async def test_create_default_user_settings_guild_enabled(db_pool):
    await insert_guild_settings(db_pool, 10, vc_stats_enabled=True)
    await create_default_user_settings(db_pool, make_member(1, 10))
    row = await db_pool.fetchrow("SELECT * FROM user_settings WHERE guild_id = 10 AND user_id = 1")
    assert row['vc_stats_enabled'] is True
    assert row['vc_detailed_stats_enabled'] is False
