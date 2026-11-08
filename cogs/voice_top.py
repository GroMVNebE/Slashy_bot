import asyncio
import io
import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

import aiohttp
import asyncpg
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from utils import *

# Подлючаем типизацию для класса Bot из launch.py, избегая циклического импорта
# Нужно для правильного определения типов в IDE
if TYPE_CHECKING:
    from launch import Bot

# Инициализируем логгер для этого модуля
logger = logging.getLogger('slashy.voice_top')

PAGE_SIZE = 5
"""### Количество участников на одной странице топа"""

TOP_MODES = {
    'total': ('voice_stats', 'SUM(s.seconds)'),
    'max_ssn': ('voice_max_sessions', 'MAX(s.max_seconds)'),
}
"""### Режимы топа: (таблица, агрегирующее выражение)
Значения подставляются в SQL-запрос, поэтому берутся только из этого словаря"""

# Параметры картинки с топом
CANVAS_SIZE = (1000, 560)
COLUMN_WIDTH = 200
BAR_WIDTH = 120
BASELINE_Y = 490
MAX_BAR_HEIGHT = 300
MIN_BAR_HEIGHT = 40
AVATAR_SIZE = 64
BACKGROUND_COLOR = '#2b2d31'
BAR_COLOR = '#5865F2'
MEDAL_COLORS = {1: '#FAA61A', 2: '#B9BBBE', 3: '#CD7F32'}
DARK_TEXT_RANKS = (1, 2)
"""### Места со светлыми столбцами, на которых подпись значения рисуется тёмным цветом"""

# Шрифты загружаются при загрузке модуля: если файла нет, ког не загрузится и ошибка будет видна сразу при запуске
NAME_FONT = ImageFont.truetype('assets/NishikiTeki-MVxaJ.ttf', 24)
VALUE_FONT = ImageFont.truetype('assets/the_weekend.otf', 20)
RANK_FONT = ImageFont.truetype('assets/the_weekend.otf', 30)


@dataclass
class TopEntry:
    """### Участник в топе"""
    rank: int
    user_id: int
    name: str
    seconds: int
    member: discord.Member | None = None


def get_bar_height(value: int, page_max: int, max_height: int = MAX_BAR_HEIGHT,
                   min_height: int = MIN_BAR_HEIGHT) -> int:
    """### Функция для вычисления высоты столбца
    Высота пропорциональна значению относительно лидера страницы, но не меньше минимальной

    Args:
        value (int): Значение участника
        page_max (int): Наибольшее значение на странице
        max_height (int, optional): Высота столбца лидера страницы
        min_height (int, optional): Минимальная высота столбца (чтобы поместилась подпись)

    Returns:
        int: Высота столбца в пикселях
    """
    if page_max <= 0:
        return min_height
    return max(min_height, round(max_height * value / page_max))


def fit_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """### Функция для обрезки текста под ширину
    Обрезает текст и добавляет '...', если он не помещается в заданную ширину

    Args:
        text (str): Исходный текст
        font (:class:`ImageFont.FreeTypeFont`): Шрифт, которым будет нарисован текст
        max_width (int): Максимальная ширина в пикселях

    Returns:
        str: Текст, помещающийся в заданную ширину
    """
    if font.getlength(text) <= max_width:
        return text
    while text and font.getlength(text + '...') > max_width:
        text = text[:-1]
    return text + '...'


def make_round_avatar(avatar: Image.Image | None, name: str) -> Image.Image:
    """### Функция для создания круглого аватара
    Если аватар не удалось загрузить, рисует серый круг с первой буквой никнейма

    Args:
        avatar (:class:`Image.Image` | None): Загруженный аватар
        name (str): Никнейм участника

    Returns:
        Image.Image: Круглый аватар размером AVATAR_SIZE x AVATAR_SIZE
    """
    mask = Image.new('L', (AVATAR_SIZE, AVATAR_SIZE), 0)
    ImageDraw.Draw(mask).ellipse(
        (0, 0, AVATAR_SIZE - 1, AVATAR_SIZE - 1), fill=255)
    if avatar is None:
        avatar = Image.new('RGBA', (AVATAR_SIZE, AVATAR_SIZE), '#4E5058')
        ImageDraw.Draw(avatar).text((AVATAR_SIZE // 2, AVATAR_SIZE // 2), (name[:1] or '?').upper(),
                                    font=NAME_FONT, fill='#FFFFFF', anchor='mm')
    else:
        avatar = avatar.convert('RGBA').resize((AVATAR_SIZE, AVATAR_SIZE))
    result = Image.new('RGBA', (AVATAR_SIZE, AVATAR_SIZE), (0, 0, 0, 0))
    result.paste(avatar, (0, 0), mask)
    return result


def render_voice_top(entries: list[TopEntry], avatars: dict[int, Image.Image]) -> io.BytesIO:
    """### Функция для отрисовки страницы топа
    Рисует столбцы участников: сверху аватар и никнейм, в столбце - значение, под столбцом - место

    Args:
        entries (list[TopEntry]): Участники текущей страницы (не больше PAGE_SIZE), по убыванию значения
        avatars (dict[int, Image.Image]): Загруженные аватары по ID участника

    Returns:
        io.BytesIO: Картинка в формате PNG
    """
    image = Image.new('RGBA', CANVAS_SIZE, BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)
    page_max = max((entry.seconds for entry in entries), default=0)
    # Центрируем колонки
    left = (CANVAS_SIZE[0] - COLUMN_WIDTH * len(entries)) // 2

    for idx, entry in enumerate(entries):
        center = left + COLUMN_WIDTH * idx + COLUMN_WIDTH // 2
        bar_top = BASELINE_Y - get_bar_height(entry.seconds, page_max)
        # Столбец со значением
        draw.rounded_rectangle(
            (center - BAR_WIDTH // 2, bar_top, center + BAR_WIDTH // 2, BASELINE_Y),
            radius=8, fill=MEDAL_COLORS.get(entry.rank, BAR_COLOR))
        draw.text((center, bar_top + 10), short_time(entry.seconds), font=VALUE_FONT,
                  fill='#1E1F22' if entry.rank in DARK_TEXT_RANKS else '#FFFFFF', anchor='mt')
        # Никнейм и аватар над столбцом
        name_y = bar_top - 10
        draw.text((center, name_y), fit_text(entry.name, NAME_FONT, COLUMN_WIDTH - 12),
                  font=NAME_FONT, fill='#DBDEE1', anchor='mb')
        avatar = make_round_avatar(avatars.get(entry.user_id), entry.name)
        image.paste(avatar, (center - AVATAR_SIZE // 2,
                    name_y - 36 - AVATAR_SIZE), avatar)
        # Место под столбцом
        draw.text((center, BASELINE_Y + 14), f'#{entry.rank}',
                  font=RANK_FONT, fill='#FFFFFF', anchor='mt')

    draw.line(
        (40, BASELINE_Y, CANVAS_SIZE[0] - 40, BASELINE_Y), fill='#4E5058', width=2)
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    buf.seek(0)
    return buf


async def get_voice_top(pool: asyncpg.Pool, guild_id: int, start: date, end: date,
                        mode: Literal['total', 'max_ssn']) -> list[tuple[int, int]]:
    """### Функция для получения топа по времени "общения" из БД
    Не включает пользователей, отключивших сбор статистики или закрывших к ней доступ

    Args:
        pool (:class:`asyncpg.Pool`): Пул соединений с БД
        guild_id (int): ID сервера
        start (date): Первый день периода
        end (date): Последний день периода
        mode (Literal['total', 'max_ssn']): Суммарное время или максимальная сессия

    Returns:
        list[tuple[int, int]]: Пары (ID пользователя, секунды) по убыванию значения
    """
    table, aggregate = TOP_MODES[mode]
    rows = await pool.fetch(
        f"""
        SELECT s.user_id, {aggregate} AS value
        FROM {table} s
        LEFT JOIN user_settings u ON u.guild_id = s.guild_id AND u.user_id = s.user_id
        WHERE s.guild_id = $1 AND s.day BETWEEN $2 AND $3
          AND u.vc_stats_enabled IS DISTINCT FROM FALSE
          AND u.vc_stats_privacy IS DISTINCT FROM FALSE
        GROUP BY s.user_id
        HAVING {aggregate} > 0
        ORDER BY value DESC, s.user_id
        """,
        guild_id, start, end)
    return [(row['user_id'], int(row['value'])) for row in rows]


def build_ranking(rows: list[tuple[int, int]], guild: discord.Guild) -> list[TopEntry]:
    """### Функция для расстановки мест в топе
    Оставляет только участников, которые сейчас на сервере и не являются ботами, и нумерует их без пропусков

    Args:
        rows (list[tuple[int, int]]): Пары (ID пользователя, секунды) по убыванию значения
        guild (:class:`discord.Guild`): Сервер, для которого составляется топ

    Returns:
        list[TopEntry]: Участники топа с местами
    """
    entries = []
    for user_id, seconds in rows:
        member = guild.get_member(user_id)
        if member is None or member.bot:
            continue
        entries.append(TopEntry(len(entries) + 1, user_id,
                       member.display_name, seconds, member))
    return entries


async def download_avatar(session: aiohttp.ClientSession, member: discord.Member) -> Image.Image | None:
    """### Функция для загрузки аватара участника

    Args:
        session (:class:`aiohttp.ClientSession`): HTTP-сессия
        member (:class:`discord.Member`): Участник сервера

    Returns:
        Image.Image | None: Аватар или None, если его не удалось загрузить
    """
    try:
        async with session.get(member.display_avatar.with_size(128).url) as response:
            if response.status != 200:
                return None
            return Image.open(io.BytesIO(await response.read())).convert('RGBA')
    except Exception as e:
        logger.warning(
            f'Не удалось загрузить аватар пользователя {get_info(member)}: {e}')
        return None


class VoiceTopView(discord.ui.View):
    """### UI-представление для топа по времени "общения"
    Содержит кнопки для смены периода, режима и страницы топа"""

    def __init__(self, bot: 'Bot', user: discord.Member, initial_interaction: discord.Interaction):
        """### UI-представление для топа по времени "общения"
        Содержит кнопки для смены периода, режима и страницы топа

        Args:
            bot (:class:`Bot`): Запущенный Дискорд-бот
            user (:class:`discord.Member`): Участник, вызвавший команду
            initial_interaction (:class:`discord.Interaction`): Взаимодействие, создавшее интерфейс
        """
        super().__init__(timeout=180.0)
        self.bot = bot
        self.user = user
        self.initial_interaction = initial_interaction
        self.period: Literal['week', 'month', 'year'] = 'week'
        self.mode: Literal['total', 'max_ssn'] = 'total'
        self.offset = 0
        self.page = 0
        self.entries: list[TopEntry] = []
        self.avatars: dict[int, Image.Image] = {}
        self.range_text = ''

    @property
    def pages_count(self) -> int:
        return max(1, math.ceil(len(self.entries) / PAGE_SIZE))

    async def on_timeout(self):
        try:
            logger.debug(
                'Время работы интерфейса топа по времени "общения" истекло')
            await self.initial_interaction.delete_original_response()
        except Exception as e:
            logger.error(
                f'Ошибка при удалении сообщения по тайм-ауту: {e}', exc_info=True)

    async def load_entries(self):
        """### Функция для загрузки топа за выбранный период и режим
        Сбрасывает страницу на первую"""
        start, end = get_period_range(self.period, self.offset, date.today())
        self.range_text = f"{start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}"
        logger.debug(f'Загрузка топа ({self.mode}, {self.period}, {self.offset}) '
                     f'для сервера {get_info(self.user.guild)}')
        rows = await get_voice_top(self.bot.db_pool, self.user.guild.id, start, end, self.mode)
        self.entries = build_ranking(rows, self.user.guild)
        self.page = 0

    async def load_avatars(self, entries: list[TopEntry]):
        """### Функция для загрузки аватаров участников, которых ещё нет в кэше"""
        missing = [
            entry for entry in entries if entry.user_id not in self.avatars and entry.member]
        if not missing:
            return
        async with aiohttp.ClientSession() as session:
            avatars = await asyncio.gather(*(download_avatar(session, entry.member) for entry in missing))
        for entry, avatar in zip(missing, avatars):
            if avatar is not None:
                self.avatars[entry.user_id] = avatar

    def update_buttons_state(self):
        self.next_period.disabled = self.offset >= 0
        for button, period in ((self.set_week, 'week'), (self.set_month, 'month'), (self.set_year, 'year')):
            button.style = discord.ButtonStyle.primary if self.period == period else discord.ButtonStyle.secondary
        for button, mode in ((self.set_total, 'total'), (self.set_max_ssn, 'max_ssn')):
            button.style = discord.ButtonStyle.primary if self.mode == mode else discord.ButtonStyle.secondary
        self.prev_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.pages_count - 1
        self.page_label.label = f'{self.page + 1}/{self.pages_count}'

    def build_footer(self) -> str:
        own = next(
            (entry for entry in self.entries if entry.user_id == self.user.id), None)
        place = f'Ваше место: {own.rank} ({short_time(own.seconds)})' if own else 'Вас нет в топе за этот период'
        return f'Страница {self.page + 1}/{self.pages_count} · {place}'

    async def build_message(self) -> tuple[discord.Embed, list[discord.File]]:
        """### Функция для создания сообщения с текущей страницей топа

        Returns:
            tuple[discord.Embed, list[discord.File]]: Embed и прикреплённые файлы
        """
        self.update_buttons_state()
        title = 'Топ по времени "общения"' if self.mode == 'total' else 'Топ по максимальной сессии "общения"'
        if not self.entries:
            embed = create_embed(title=title, description=f'📅 **Период**: {self.range_text}\n'
                                 'За этот период нет данных', color=discord.Color.blurple())
            return embed, []
        page_entries = self.entries[self.page *
                                    PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        await self.load_avatars(page_entries)
        buf = await asyncio.to_thread(render_voice_top, page_entries, self.avatars)
        embed = create_embed(title=title, description=f'📅 **Период**: {self.range_text}',
                             image_url='attachment://voice_top.png', footer_text=self.build_footer(),
                             color=discord.Color.blurple())
        return embed, [discord.File(buf, filename='voice_top.png')]

    async def show(self, interaction: discord.Interaction, reload: bool = False):
        """### Функция для отображения текущей страницы топа

        Args:
            interaction (:class:`discord.Interaction`): Взаимодействие, на которое нужно ответить
            reload (bool, optional): Загрузить топ из БД заново (при смене периода или режима)
        """
        # Загрузка аватаров и отрисовка может занять больше 3 секунд, поэтому сразу откладываем ответ
        await interaction.response.defer(ephemeral=True)
        if reload:
            await self.load_entries()
        embed, files = await self.build_message()
        await interaction.edit_original_response(embed=embed, attachments=files, view=self)

    @discord.ui.button(emoji='⬅️', style=discord.ButtonStyle.secondary, row=0)
    async def prev_period(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.offset -= 1
        await self.show(interaction, reload=True)

    @discord.ui.button(label='-', style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def period_fill(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(emoji='➡️', style=discord.ButtonStyle.secondary, row=0)
    async def next_period(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.offset < 0:
            self.offset += 1
            await self.show(interaction, reload=True)

    @discord.ui.button(label='Неделя', style=discord.ButtonStyle.primary, row=1)
    async def set_week(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.period, self.offset = 'week', 0
        await self.show(interaction, reload=True)

    @discord.ui.button(label='Месяц', style=discord.ButtonStyle.secondary, row=1)
    async def set_month(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.period, self.offset = 'month', 0
        await self.show(interaction, reload=True)

    @discord.ui.button(label='Год', style=discord.ButtonStyle.secondary, row=1)
    async def set_year(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.period, self.offset = 'year', 0
        await self.show(interaction, reload=True)

    @discord.ui.button(label='Общее время', style=discord.ButtonStyle.primary, row=2)
    async def set_total(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.mode = 'total'
        await self.show(interaction, reload=True)

    @discord.ui.button(label='Макс. сессия', style=discord.ButtonStyle.secondary, row=2)
    async def set_max_ssn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.mode = 'max_ssn'
        await self.show(interaction, reload=True)

    @discord.ui.button(emoji='◀️', style=discord.ButtonStyle.secondary, row=3)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            await self.show(interaction)

    @discord.ui.button(label='1/1', style=discord.ButtonStyle.secondary, disabled=True, row=3)
    async def page_label(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(emoji='▶️', style=discord.ButtonStyle.secondary, row=3)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.pages_count - 1:
            self.page += 1
            await self.show(interaction)


class VoiceTop(commands.Cog):
    """### Модуль с топом участников по времени "общения"

    - Команда :meth:`voice_top` для просмотра топа по суммарному времени и максимальной сессии"""

    def __init__(self, bot: 'Bot'):
        self.bot = bot

    @app_commands.command(name='voice-top', description='Показывает топ участников сервера по времени "общения"')
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: (i.guild_id, i.user.id))
    async def voice_top(self, interaction: discord.Interaction):
        """### Команда для просмотра топа по времени "общения" """
        if not self.bot.db_pool or not isinstance(interaction.user, discord.Member):
            return
        logger.info(
            f'Пользователь {get_info(interaction.user)} вызвал команду /voice-top')
        try:
            guild_set = await self.bot.db_pool.fetchrow(
                "SELECT vc_stats_enabled FROM guild_settings WHERE guild_id = $1", interaction.guild_id)
            if not guild_set or not guild_set['vc_stats_enabled']:
                embed = create_embed(
                    title='Ошибка!',
                    description='На данном сервере не разрешён сбор **статистики времени "общения"**',
                    color=discord.Color.red())
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            view = VoiceTopView(self.bot, interaction.user, interaction)
            await view.show(interaction, reload=True)
        except Exception as e:
            logger.error(f'Ошибка в команде /voice-top для пользователя {get_info(interaction.user)}: {e}',
                         exc_info=True)
            embed = create_embed(
                title='Произошла ошибка',
                description='Не удалось получить топ по времени "общения". Попробуйте позже',
                color=discord.Color.red())
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(VoiceTop(bot))
