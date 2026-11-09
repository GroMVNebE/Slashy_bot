from utils import create_embed, get_info
from matplotlib.ticker import MaxNLocator
import matplotlib.pyplot as plt
import asyncio
import io
import logging
import math
from typing import TYPE_CHECKING, Literal

import asyncpg
import discord
import matplotlib
import numpy as np
matplotlib.use('Agg')


# Подлючаем типизацию для класса Bot из launch.py, избегая циклического импорта
if TYPE_CHECKING:
    from launch import Bot

# Инициализируем логгер для этого модуля
logger = logging.getLogger('slashy.bot_stats')
logging.getLogger('matplotlib').setLevel(logging.WARNING)

StatKind = Literal['messages', 'sessions', 'commands']

STAT_TABLES: dict[str, tuple[str, str]] = {
    'messages': ('message_length_stats', 'length'),
    'sessions': ('session_duration_stats', 'minutes'),
    'commands': ('command_usage_stats', 'command'),
}
"""### Таблицы анонимной статистики: вид статистики -> (таблица, столбец значения)
Значения подставляются в SQL-запросы, поэтому берутся только из этого словаря"""

PERCENTILES = (25, 50, 75, 90, 95, 99)
COMMANDS_PER_PAGE = 10
GUILDS_IN_SELECT = 24
"""### Количество серверов в списке выбора (ещё один вариант - "Все серверы", всего не больше 25)"""

TABS = {
    'messages': ('Длина сообщений', 'Длина сообщения (символы)', '#5865F2'),
    'sessions': ('Сессии', 'Время сессии (минуты)', '#FAA61A'),
    'commands': ('Команды', None, None),
}
"""### Вкладки статистики: вид -> (название, подпись оси X, цвет графика)"""


async def _record(pool: asyncpg.Pool, kind: StatKind, guild_id: int, value: int | str) -> None:
    """### Функция для записи значения в анонимную статистику
    Увеличивает счётчик значения за текущий день. Ничего не записывает, если сервер отключил сбор статистики.
    Ошибки записи только логируются, чтобы не мешать основной обработке

    Args:
        pool (:class:`asyncpg.Pool`): Пул соединений с БД
        kind (StatKind): Вид статистики
        guild_id (int): ID сервера
        value (int | str): Записываемое значение (длина сообщения, минуты сессии или имя команды)
    """
    table, column = STAT_TABLES[kind]
    try:
        await pool.execute(
            f"""
            INSERT INTO {table} (guild_id, {column})
            SELECT $1, $2
            WHERE NOT EXISTS (
                SELECT 1 FROM guild_settings WHERE guild_id = $1 AND anon_stats_enabled = FALSE
            )
            ON CONFLICT (guild_id, day, {column}) DO UPDATE SET count = {table}.count + 1
            """,
            guild_id, value)
    except Exception as e:
        logger.warning(f'Ошибка при записи статистики {kind} для сервера {get_info(guild_id)}: {e}',
                       exc_info=True)


async def record_message_length(pool: asyncpg.Pool, guild_id: int, length: int) -> None:
    """### Функция для записи длины сообщения в анонимную статистику"""
    await _record(pool, 'messages', guild_id, length)


async def record_session_duration(pool: asyncpg.Pool, guild_id: int, minutes: int) -> None:
    """### Функция для записи продолжительности сессии "общения" (в минутах) в анонимную статистику"""
    await _record(pool, 'sessions', guild_id, minutes)


async def record_command_usage(pool: asyncpg.Pool, guild_id: int, command: str) -> None:
    """### Функция для записи использования команды в анонимную статистику"""
    await _record(pool, 'commands', guild_id, command)


async def get_stat_counts(pool: asyncpg.Pool, kind: StatKind, guild_id: int | None) -> list[tuple]:
    """### Функция для получения статистики за всё время
    Суммирует счётчики по дням для каждого значения

    Args:
        pool (:class:`asyncpg.Pool`): Пул соединений с БД
        kind (StatKind): Вид статистики
        guild_id (int | None): ID сервера или None для всех серверов

    Returns:
        list[tuple]: Пары (значение, кол-во). Для команд - по убыванию кол-ва, иначе - по возрастанию значения
    """
    table, column = STAT_TABLES[kind]
    order = 'count DESC, value' if kind == 'commands' else 'value'
    rows = await pool.fetch(
        f"""
        SELECT {column} AS value, SUM(count) AS count
        FROM {table}
        WHERE $1::BIGINT IS NULL OR guild_id = $1
        GROUP BY {column}
        ORDER BY {order}
        """,
        guild_id)
    return [(row['value'], int(row['count'])) for row in rows]


async def get_guilds_with_stats(pool: asyncpg.Pool) -> list[tuple[int, int]]:
    """### Функция для получения серверов, по которым есть статистика

    Returns:
        list[tuple[int, int]]: Пары (ID сервера, общее кол-во записанных событий) по убыванию объёма данных
    """
    union = ' UNION ALL '.join(
        f'SELECT guild_id, count FROM {table}' for table, _ in STAT_TABLES.values())
    rows = await pool.fetch(
        f"""
        SELECT guild_id, SUM(count) AS total
        FROM ({union}) AS stats
        GROUP BY guild_id
        ORDER BY total DESC, guild_id
        """)
    return [(row['guild_id'], int(row['total'])) for row in rows]


def describe(values: list[int], counts: list[int], bins: int = 40) -> dict:
    """### Функция для расчёта основных характеристик распределения
    Мода для непрерывных данных - середина самого высокого столбца гистограммы

    Args:
        values (list[int]): Значения
        counts (list[int]): Сколько раз встретилось каждое значение
        bins (int, optional): Кол-во столбцов гистограммы для расчёта моды

    Returns:
        dict: n, mean, std, median, mode и перцентили p25 ... p99
    """
    arr = np.repeat(np.asarray(values, dtype=float), counts)
    hist, edges = np.histogram(arr, bins=bins)
    peak = hist.argmax()
    stats = {
        'n': len(arr),
        'mean': float(arr.mean()),
        'std': float(arr.std()),
        'median': float(np.median(arr)),
        'mode': float((edges[peak] + edges[peak + 1]) / 2),
    }
    for p in PERCENTILES:
        stats[f'p{p}'] = float(np.percentile(arr, p))
    return stats


def format_stats(stats: dict) -> str:
    """### Функция для вывода характеристик распределения текстом (для Embed)"""
    percentiles = '  '.join(f'p{p}={stats[f"p{p}"]:.1f}' for p in PERCENTILES)
    return (f'**Кол-во:** {stats["n"]}\n'
            f'**Среднее:** {stats["mean"]:.1f}  **σ:** {stats["std"]:.1f}\n'
            f'**Медиана:** {stats["median"]:.1f}  **Мода:** ≈{stats["mode"]:.1f}\n'
            f'**Перцентили:** {percentiles}')


def render_distribution(values: list[int], counts: list[int], xlabel: str, color: str) -> io.BytesIO:
    """### Функция для отрисовки распределения
    Гистограмма (обрезанная по 99-му перцентилю, чтобы выбросы не "сплющивали" основную часть)
    с линиями среднего, медианы и моды, и боксплот под ней

    Args:
        values (list[int]): Значения
        counts (list[int]): Сколько раз встретилось каждое значение
        xlabel (str): Подпись оси X
        color (str): Цвет гистограммы

    Returns:
        io.BytesIO: Картинка в формате PNG
    """
    stats = describe(values, counts)
    values_arr = np.asarray(values, dtype=float)
    counts_arr = np.asarray(counts)
    limit = stats['p99']
    shown = values_arr <= limit
    hidden = int(counts_arr[~shown].sum())

    fig, (ax_hist, ax_box) = plt.subplots(
        2, 1, figsize=(9, 5.5), dpi=120, sharex=True,
        gridspec_kw={'height_ratios': [4, 1], 'hspace': 0.08})
    fig.patch.set_facecolor('#2b2d31')
    for ax in (ax_hist, ax_box):
        ax.set_facecolor('#1e1f22')
        ax.tick_params(colors='#B9BBBE', labelsize=9)
        for spine in ax.spines.values():
            spine.set_visible(False)

    ax_hist.hist(values_arr[shown], bins=40, weights=counts_arr[shown], color=color,
                 alpha=0.85, edgecolor='#1e1f22')
    for key, line_color, style, label in (('mean', '#FFFFFF', '--', 'Среднее'),
                                          ('median', '#3BA55D', '-', 'Медиана'),
                                          ('mode', '#EB459E', ':', 'Мода ≈')):
        ax_hist.axvline(stats[key], color=line_color, linestyle=style, linewidth=1.8,
                        label=f'{label} {stats[key]:.1f}')
    for p, style in ((90, '--'), (95, '-.')):
        ax_hist.axvline(stats[f'p{p}'], color='#B9BBBE',
                        linestyle=style, alpha=0.6)
        ax_hist.text(stats[f'p{p}'], 0.97, f'p{p}', rotation=90, va='top', ha='right', fontsize=8,
                     color='#B9BBBE', transform=ax_hist.get_xaxis_transform())
    ax_hist.set_title(f'{xlabel}\nn={stats["n"]}, σ={stats["std"]:.1f}'
                      + (f', скрыто выбросов: {hidden}' if hidden else ''),
                      color='#FFFFFF', fontsize=11)
    ax_hist.set_ylabel('Количество', color='#DBDEE1')
    ax_hist.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax_hist.grid(axis='y', linestyle='--', alpha=0.2, color='#FFFFFF')
    ax_hist.legend(fontsize=9, facecolor='#2b2d31',
                   edgecolor='#2b2d31', labelcolor='#DBDEE1')

    sample = np.repeat(values_arr, counts_arr)
    box = ax_box.boxplot(sample, orientation='horizontal', whis=(
        5, 95), showfliers=False, patch_artist=True, widths=0.6)
    for patch in box['boxes']:
        patch.set(facecolor=color, alpha=0.6)
    for item in box['whiskers'] + box['caps'] + box['medians']:
        item.set(color='#DBDEE1')
    ax_box.set_yticks([])
    ax_box.set_xlabel(xlabel, color='#DBDEE1')
    ax_box.set_xlim(left=0, right=max(limit * 1.05, 1))
    ax_box.grid(axis='x', linestyle='--', alpha=0.2, color='#FFFFFF')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight',
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


class GuildSelect(discord.ui.Select):
    """### Выпадающий список для выбора сервера, статистику которого нужно показать"""

    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(placeholder='Выберите сервер...', min_values=1, max_values=1,
                         options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: StatsView = self.view
        value = self.values[0]
        view.guild_id = None if value == 'all' else int(value)
        view.page = 0
        await view.show(interaction)


class StatsView(discord.ui.View):
    """### UI-представление для просмотра анонимной статистики серверов
    Содержит выбор сервера, вкладки (длина сообщений / сессии / команды) и листание списка команд"""

    def __init__(self, bot: 'Bot', interaction: discord.Interaction, guilds: list[tuple[int, int]]):
        """### UI-представление для просмотра анонимной статистики серверов

        Args:
            bot (:class:`Bot`): Запущенный Дискорд-бот
            interaction (:class:`discord.Interaction`): Взаимодействие, открывшее статистику
            guilds (list[tuple[int, int]]): Серверы со статистикой из :func:`get_guilds_with_stats`
        """
        super().__init__(timeout=300.0)
        self.bot = bot
        self.initial_interaction = interaction
        self.owner_id = interaction.user.id
        self.tab: StatKind = 'messages'
        self.guild_id: int | None = None
        self.page = 0
        self.commands: list[tuple[str, int]] = []
        options = [discord.SelectOption(
            label='Все серверы', value='all', default=True)]
        for guild_id, total in guilds[:GUILDS_IN_SELECT]:
            options.append(discord.SelectOption(
                label=self.guild_name(guild_id)[:100], value=str(guild_id), description=f'Событий: {total}'))
        self.guild_select = GuildSelect(options)
        self.add_item(self.guild_select)

    @classmethod
    async def create(cls, bot: 'Bot', interaction: discord.Interaction) -> 'StatsView':
        """### Функция для создания интерфейса статистики со списком серверов из БД"""
        return cls(bot, interaction, await get_guilds_with_stats(bot.db_pool))

    def guild_name(self, guild_id: int | None) -> str:
        if guild_id is None:
            return 'Все серверы'
        guild = self.bot.get_guild(guild_id)
        return guild.name if guild else f'Неизвестный сервер (ID {guild_id})'

    @property
    def pages_count(self) -> int:
        return max(1, math.ceil(len(self.commands) / COMMANDS_PER_PAGE))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    async def on_timeout(self):
        try:
            logger.debug(
                'Время работы интерфейса анонимной статистики истекло')
            await self.initial_interaction.delete_original_response()
        except discord.NotFound:
            pass
        except Exception as e:
            logger.error(
                f'Ошибка при удалении сообщения по тайм-ауту: {e}', exc_info=True)

    def update_items(self):
        """### Функция для обновления кнопок: подсветка вкладки, выбранный сервер, кнопки страниц"""
        for button, tab in ((self.tab_messages, 'messages'), (self.tab_sessions, 'sessions'),
                            (self.tab_commands, 'commands')):
            button.style = discord.ButtonStyle.primary if self.tab == tab else discord.ButtonStyle.secondary
        selected = 'all' if self.guild_id is None else str(self.guild_id)
        for option in self.guild_select.options:
            option.default = option.value == selected
        # Кнопки страниц нужны только на вкладке с командами
        for button in (self.prev_page, self.page_label, self.next_page):
            self.remove_item(button)
        if self.tab == 'commands':
            self.prev_page.disabled = self.page <= 0
            self.next_page.disabled = self.page >= self.pages_count - 1
            self.page_label.label = f'{self.page + 1}/{self.pages_count}'
            for button in (self.prev_page, self.page_label, self.next_page):
                self.add_item(button)

    async def build_message(self) -> tuple[discord.Embed, list[discord.File]]:
        """### Функция для создания сообщения с текущей вкладкой статистики"""
        title = f'Статистика: {TABS[self.tab][0].lower()} — {self.guild_name(self.guild_id)}'
        data = await get_stat_counts(self.bot.db_pool, self.tab, self.guild_id)
        if self.tab == 'commands':
            self.commands = data
            self.page = min(self.page, self.pages_count - 1)
        self.update_items()
        if not data:
            return create_embed(title=title, description='Данных пока нет', color=discord.Color.blurple()), []

        if self.tab == 'commands':
            total = sum(count for _, count in data)
            first = self.page * COMMANDS_PER_PAGE
            lines = [f'{first + idx + 1}. `/{name}` — {count} ({count / total:.0%})'
                     for idx, (name, count) in enumerate(data[first:first + COMMANDS_PER_PAGE])]
            embed = create_embed(title=title, description='\n'.join(lines), color=discord.Color.blurple(),
                                 footer_text=f'Страница {self.page + 1}/{self.pages_count} · всего вызовов: {total}')
            return embed, []

        values = [value for value, _ in data]
        counts = [count for _, count in data]
        _, xlabel, color = TABS[self.tab]
        buf = await asyncio.to_thread(render_distribution, values, counts, xlabel, color)
        embed = create_embed(title=title, description=format_stats(describe(values, counts)),
                             image_url='attachment://stats.png', color=discord.Color.blurple())
        return embed, [discord.File(buf, filename='stats.png')]

    async def show(self, interaction: discord.Interaction):
        """### Функция для отображения текущей вкладки статистики"""
        await interaction.response.defer()
        logger.debug(f'Отображение статистики {self.tab} для сервера '
                     f'{get_info(self.guild_id) if self.guild_id else "все серверы"}')
        embed, files = await self.build_message()
        await interaction.edit_original_response(embed=embed, attachments=files, view=self)

    @discord.ui.button(label='Длина сообщений', style=discord.ButtonStyle.primary, row=1)
    async def tab_messages(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.tab, self.page = 'messages', 0
        await self.show(interaction)

    @discord.ui.button(label='Сессии', style=discord.ButtonStyle.secondary, row=1)
    async def tab_sessions(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.tab, self.page = 'sessions', 0
        await self.show(interaction)

    @discord.ui.button(label='Команды', style=discord.ButtonStyle.secondary, row=1)
    async def tab_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.tab, self.page = 'commands', 0
        await self.show(interaction)

    @discord.ui.button(emoji='◀️', style=discord.ButtonStyle.secondary, row=2)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        await self.show(interaction)

    @discord.ui.button(label='1/1', style=discord.ButtonStyle.secondary, disabled=True, row=2)
    async def page_label(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(emoji='▶️', style=discord.ButtonStyle.secondary, row=2)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.pages_count - 1:
            self.page += 1
        await self.show(interaction)
