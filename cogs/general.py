import matplotlib.ticker as ticker
import matplotlib.pyplot as plt
import aiohttp
import asyncpg
import discord
import logging
import io
from datetime import timedelta, date, datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont
from discord import app_commands
from discord.ext import commands
from typing import TYPE_CHECKING
from utils import *
from random import randint
import matplotlib
matplotlib.use('Agg')

# Подлючаем типизацию для класса Bot из launch.py, избегая циклического импорта
# Нужно для правильного определения типов в IDE
if TYPE_CHECKING:
    from launch import Bot

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# Инициализируем логгер для этого модуля
logger = logging.getLogger('slashy.general')
logging.getLogger('matplotlib').setLevel(logging.WARNING)

PRIVACY_CATEGORIES: dict[str, dict] = {
    'levels': {'label': 'Уровень и опыт',
               'description': 'Опыт и уровень на всех серверах',
               'tables': ['user_levels']},
    'guess': {'label': 'Игра "Угадай число"',
              'description': 'Незавершённые игры',
              'tables': ['guess_number']},
    'voice': {'label': 'Статистика общения',
              'description': 'Время по дням, максимальные и детальные сессии',
              'tables': ['voice_stats', 'voice_max_sessions']},
    'detailed': {'label': 'Детальные сессии',
                 'description': 'Время начала и окончания сессий',
                 'tables': []},
}
"""Категории данных для /privacy"""


def _affected(status: str) -> int:
    """### Функция для подсчёта строк, затронутых SQL-запросом
    Возвращает число затронутых строк из статуса asyncpg ('DELETE 3' -> 3)

    Args:
        status (str): Статус запроса asyncpg

    Returns:
        int: Количество затронутых строк"""
    return int(status.split()[-1])


async def delete_user_data(bot: "Bot", user_id: int, categories: set[str], guild_id: int = None) -> int:
    """### Функция для удаления пользовательских данных из БД
    Удаляет пользовательские данные на выбранном сервере или на всех серверах

    Args:
        bot (:class:`Bot`): Запущенный Discord-бот
        user_id (int): UserID пользователя в Discord
        guild_id (int): GuildID сервера в Discord
        categories (set[str]): Категории удаляемых данных

    Returns:
        int: Количество удалённых строк
    """
    cats = set(categories)
    # Детальные сессии ищутся через voice_stats, поэтому удаляем их вместе
    if 'voice' in cats:
        cats.add('detailed')
        # Чистим активные сессии в памяти, иначе автосохранение вернёт данные
        events = bot.get_cog('Events')
        if events:
            events.active_sessions.pop(user_id, None)
            events.pending_sessions.pop(user_id, None)

    total = 0
    async with bot.db_pool.acquire() as con:
        async with con.transaction():
            if 'detailed' in cats:
                if guild_id is None:
                    rows = await con.fetch(
                        "SELECT DISTINCT guild_id FROM voice_stats WHERE user_id = $1",
                        user_id)
                    uids = [get_voice_hashes(user_id, r['guild_id'])[
                        0] for r in rows]
                    if uids:
                        status = await con.execute(
                            "DELETE FROM voice_detailed_sessions WHERE user_id = ANY($1::text[])",
                            uids)
                        total += _affected(status)
                else:
                    uid, gid = get_voice_hashes(user_id, guild_id)
                    status = await con.execute("DELETE FROM voice_detailed_sessions WHERE user_id = $1 AND guild_id = $2", uid, gid)
                    total += _affected(status)
            for key in cats:
                for table in PRIVACY_CATEGORIES[key]['tables']:
                    if guild_id is None:
                        status = await con.execute(
                            f"DELETE FROM {table} WHERE user_id = $1", user_id)
                    else:
                        status = await con.execute(
                            f"DELETE FROM {table} WHERE user_id = $1 AND guild_id = $2", user_id, guild_id)
                    total += _affected(status)
    return total


def _confirm_embed(categories: set[str], guild_id: int = None) -> discord.Embed:
    """### Функция, генерирующая сообщение для подтверждения удаления данных

    Args:
        categories (set[str]): Категории удаляемых данных
        guild_id (int): GuildID сервера в Discord

    Returns:
        discord.Embed: Сообщение для подтверждения удаления данных
    """
    names = [PRIVACY_CATEGORIES[k]['label']
             for k in PRIVACY_CATEGORIES if k in categories]
    if 'voice' in categories and 'detailed' not in categories:
        names.append(PRIVACY_CATEGORIES['detailed']['label'])
    descr = (f'Будут удалены данные {"**на всех серверах**" if guild_id is None else "на текущем сервере"}:\n• ' + '\n• '.join(names) +
             '\n\nДействие **необратимо**.')
    return create_embed(title='Подтвердите удаление', description=descr,
                        color=discord.Color.orange())


class PrivacyBaseView(discord.ui.View):
    """### Базовое UI-представление для команды /privacy
    Формирует основу для других UI-представлений"""

    def __init__(self, bot: "Bot", interaction: discord.Interaction, timeout: float = 180.0):
        """### Базовое UI-представление для команды /privacy
        Формирует основу для других UI-представлений

        Args:
            bot (:class:`Bot`): Запущенный Discord-бот
            interaction (:class:`discord.Interaction`): Взаимодействие, создавшее интерфейс
            timeout (float, optional): Таймаут, после которого UI-представление будет удалено. По умолочанию 180 сек.
        """
        super().__init__(timeout=timeout)
        self.bot = bot
        self.initial_interaction = interaction
        self.owner_id = interaction.user.id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    async def on_timeout(self):
        try:
            await self.initial_interaction.delete_original_response()
        except discord.NotFound:
            pass
        except Exception as e:
            logger.error(
                f'Ошибка при удалении панели /privacy по тайм-ауту: {e}', exc_info=True)


class PrivacyConfirmView(PrivacyBaseView):
    """### UI-представление для подтверждения удаления данных
        Содержит кнопки для удаления данных и отмены удаления"""

    def __init__(self, bot: "Bot", interaction: discord.Interaction, categories: set[str], guild_id: int = None):
        """### UI-представление для подтверждения удаления данных
        Содержит кнопки для удаления данных и отмены удаления

        Args:
            bot (:class:`Bot`): Запущенный Discord-бот
            interaction (:class:`discord.Interaction`): Взаимодействие, создавшее интерфейс
            categories (set[str]): Категории удаляемых данных
            guild_id (int): GuildID текущего Discord-сервера
        """
        super().__init__(bot, interaction, timeout=60.0)
        self.categories = categories
        self.guild_id = guild_id

    @discord.ui.button(label='Подтвердить удаление', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        # Сразу убираем кнопки, чтобы нельзя было нажать повторно
        await interaction.response.edit_message(
            embed=create_embed(title='Удаление...', color=discord.Color.orange()), view=None)
        try:
            if not self.bot.db_pool:
                raise RuntimeError('Пул соединений с БД не инициализирован')
            count = await delete_user_data(self.bot, interaction.user.id, self.categories, self.guild_id)
            logger.info(f'Пользователь {get_info(interaction.user)} удалил свои данные '
                        f'({sorted(self.categories)}), удалено строк: {count}')
            descr = (f'Удалено записей: **{count}**\n'
                     'Файлы логов (хранятся до 30 дней, ID в них хэшированы) и резервные копии БД '
                     'этим действием не затрагиваются')
            embed = create_embed(title='Данные удалены', description=descr,
                                 color=discord.Color.green())
        except Exception as e:
            logger.error(f'Ошибка при удалении данных пользователя {get_info(interaction.user)}: {e}',
                         exc_info=True)
            embed = create_embed(title='Ошибка!',
                                 description='Не удалось удалить данные.'
                                             'Попробуйте повторить попытку позднее',
                                 color=discord.Color.red())
        await interaction.edit_original_response(embed=embed, view=None)

    @discord.ui.button(label='Отмена', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            embed=create_embed(title='Удаление отменено', color=discord.Color.blue()), view=None)


class PrivacySelect(discord.ui.Select):
    """### Выпадающий список для выбора категории удаляемых данных
        Содержит меню выбора категории данных"""

    def __init__(self, guild_id: int = None):
        """### UI-представление для выбора категории удаляемых данных
        Содержит меню выбора категории данных

        Args:
            guild_id (int): GuildID текущего Discord-сервера
        """
        options = [discord.SelectOption(label=v['label'], description=v['description'], value=k)
                   for k, v in PRIVACY_CATEGORIES.items()]
        super().__init__(placeholder='Выберите, что удалить...', min_values=1,
                         max_values=len(options), options=options)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        view: PrivacyBaseView = self.view
        categories = set(self.values)
        confirm = PrivacyConfirmView(
            view.bot, view.initial_interaction, categories, self.guild_id)
        view.stop()
        await interaction.response.edit_message(embed=_confirm_embed(categories, self.guild_id), view=confirm)


class PrivacySelectView(PrivacyBaseView):
    """### UI-представление для выбора категории удаляемых данных
        Содержит выпадающий список :class:`PrivacySelect` для выбора категории данных"""

    def __init__(self, bot: "Bot", interaction: discord.Interaction, guild_id: int = None):
        """### UI-представление для выбора категории удаляемых данных
        Содержит выпадающий список :class:`PrivacySelect` для выбора категории данных

        Args:
            bot (:class:`Bot`): Запущенный Discord-бот
            interaction (:class:`discord.Interaction`): Взаимодействие, создавшее интерфейс
            guild_id (int): GuildID текущего Discord-сервера
        """
        super().__init__(bot, interaction)
        self.add_item(PrivacySelect(guild_id))


class PrivacyView(PrivacyBaseView):
    """### UI-представление для удаления данных
        Содержит кнопки для удаления выбранных категорий данных на текущем сервере, всех данных на текущем сервере и всех данных на всех серверах"""
    @discord.ui.button(label='Удалить выбранное', style=discord.ButtonStyle.danger)
    async def delete_selected(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = PrivacySelectView(
            self.bot, self.initial_interaction, interaction.guild_id)
        self.stop()
        embed = create_embed(title='Что удалить?',
                             description='Выберите одну или несколько категорий. '
                                         'Удаление затронет только текущий сервер',
                             color=discord.Color.blue())
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label='Удалить ВСЕ данные на ЭТОМ сервере', style=discord.ButtonStyle.danger)
    async def delete_all_on_guild(self, interaction: discord.Interaction, button: discord.ui.Button):
        categories = set(PRIVACY_CATEGORIES)
        view = PrivacyConfirmView(
            self.bot, self.initial_interaction, categories, interaction.guild_id)
        self.stop()
        await interaction.response.edit_message(embed=_confirm_embed(categories, interaction.guild_id), view=view)

    @discord.ui.button(label='Удалить ВСЕ данные на ВСЕХ серверах', style=discord.ButtonStyle.danger)
    async def delete_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        categories = set(PRIVACY_CATEGORIES)
        view = PrivacyConfirmView(
            self.bot, self.initial_interaction, categories)
        self.stop()
        await interaction.response.edit_message(embed=_confirm_embed(categories), view=view)


class General(commands.Cog):
    """### Модуль с общими командами

    - Команда :meth:`lvl` для вывода карточки с уровнем пользователя
    - Команда :meth:`guess` для игры "Угадай число"
    - Команда :meth:`rand` для генерации случайного числа в заданном диапазоне
    - Команда :meth:`voice` для вывода статистики времени \"общения\"
    - Команда :meth:`sessions` для вывода детальных сессий \"общения\""""

    def __init__(self, bot: "Bot"):
        self.bot = bot
        self.exp_font = ImageFont.truetype('assets/the_weekend.otf', 24)
        self.lvl_font = ImageFont.truetype('assets/the_weekend.otf', 126)
        self.username_font = ImageFont.truetype(
            'assets/NishikiTeki-MVxaJ.ttf', 36)

    @app_commands.command(name='lvl', description='Выводит Ваш текущий уровень')
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id, i.user.id))
    async def lvl(self, interaction: discord.Interaction):
        """### Выводит данные об уровне и опыте пользователя
        Выводит карточку с аватаром, никнеймом, уровнем, опытом и прогрессом до следующего уровня пользователя,
        вызвавшего команду

        Args:
            interaction (discord.Interaction): Объект взаимодействия, содержащий подробные данные об отправленной команде
        """
        logger.info(
            f'Пользователь {get_info(interaction.user)} вызвал команду /lvl')
        # Проверяем, что пул соединений с базой данных инициализирован
        if not self.bot.db_pool:
            logger.error(
                'Пул соединений с базой данных не инициализирован. Пропуск обработки команды lvl'
            )
            embed = create_embed(
                title='Ошибка!',
                description=f'В процессе создания карточки с Вашим уровнем произошла ошибка. Пожалуйста, попробуйте позже',
                color=discord.Color.red(),
            )
            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )
            return
        user = interaction.user
        # Отправляем ответ, что бот обрабатывает запрос (может занять некоторое время)
        await interaction.response.defer(ephemeral=True)
        # Получаем данные об уровне и опыте пользователя из базы данных
        logger.debug(
            f'Получение данных об уровне пользователя {get_info(user)} на сервере {get_info(interaction.guild)} из БД')
        try:
            async with self.bot.db_pool.acquire() as connection:
                row = await connection.fetchrow(
                    """
                    SELECT xp, level FROM user_levels
                    WHERE guild_id = $1 AND user_id = $2
                """,
                    interaction.guild_id,
                    user.id,
                )
                if not row:
                    xp, lvl = 0, 1
                    await connection.execute(
                        """
                        INSERT INTO user_levels (guild_id, user_id)
                        VALUES ($1, $2)
                    """,
                        interaction.guild_id,
                        user.id,
                    )
                else:
                    xp, lvl = row['xp'], row['level']
            logger.debug(
                f'Получение данных об уровне пользователя {get_info(user)} на сервере {get_info(interaction.guild)} из БД: Успешно')
        except Exception as e:
            logger.error(
                f'Ошибка при получении данных об уровне пользователя {get_info(user)} на сервере {get_info(interaction.guild)} из БД: {e}', exc_info=True
            )
            embed = create_embed(
                title='Ошибка!',
                description=f'В процессе создания карточки с Вашим уровнем произошла ошибка. Пожалуйста, попробуйте позже',
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
            return
        # Вычисляем прогресс до следующего уровня
        required_xp = get_required_xp(lvl)
        lvl_up_progress = xp / required_xp if required_xp > 0 else 0
        lvl_up_progress = min(lvl_up_progress, 1)  # Ограничиваем от 0 до 1
        logger.debug(
            f'Создание карточки уровня для пользователя {get_info(user)} на сервере {get_info(interaction.guild)}')
        try:
            # Загружаем шаблон карточки уровня
            bg = Image.open('assets/lvl_templ.png')
            # Получаем аватар пользователя, изменяем размер и вставляем на шаблон
            url = user.display_avatar.url
            ava = None
            ava_buff = None
            logger.debug(
                f'Загрузка аватара пользователя {get_info(user)} для карточки уровня')
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.read()
                        ava_buff = io.BytesIO(data)
                        ava = Image.open(ava_buff).convert('RGBA')
            if ava is None or ava_buff is None:
                logger.error(
                    f'Не удалось загрузить аватар пользователя {get_info(user)}', exc_info=True
                )
                embed = create_embed(
                    title='Ошибка!',
                    description=f'В процессе создания карточки с Вашим уровнем произошла ошибка. Пожалуйста, попробуйте позже',
                    color=discord.Color.red(),
                )
                await interaction.followup.send(embed=embed)
                return
            logger.debug(
                f'Вставляем данные пользователя {get_info(user)} на шаблон карточки')
            ava = ava.resize(size=[65, 65])
            bg.paste(ava, (90, 138))
            # Получаем имя пользователя и обрезаем его, если оно слишком длинное для отображения на карточке
            name = user.display_name
            while self.username_font.getlength(name) > 325:
                name = name[:-1]
            if name != user.display_name:
                name = name + '...'
            # Наачинаем изменение шаблона
            draw = ImageDraw.Draw(bg)
            # Вставляем на карточку
            draw.text(
                (167, 170), name, font=self.username_font, fill='#aed581', anchor='lm'
            )
            # Вставляем уровень
            draw.text(
                (194, 325), str(lvl), font=self.lvl_font, fill='#aed581', anchor='mm'
            )
            # Создаём круглый прогресс-бар, убирая лишнюю часть круга из шаблона
            draw.arc(
                (335, 217, 530, 412),
                start=-90 + int(360 * lvl_up_progress),
                end=270,
                fill='#1d262a',
                width=36,
            )
            # Вставляет опыт
            draw.text(
                (432, 316),
                f'{xp}/{required_xp}',
                font=self.exp_font,
                fill='#aed581',
                anchor='mm',
            )
            logger.debug(
                f'Сохраняем карточку уровня в буфер для отправки пользователю {get_info(user)}')
            # Сохраняем получившуюся карточку
            buff = io.BytesIO()
            bg.save(buff, format='PNG')
            buff.seek(0)
            card = discord.File(buff, filename=f'lvl_card_{user.id}.png')
            # Добавляем карточку в Embed
            embed = create_embed(
                title=f'Данные об уровне {user.display_name}',
                image_url=f'attachment://lvl_card_{user.id}.png',
            )
            # Отправляем Embed с карточкой пользователю
            await interaction.followup.send(embed=embed, file=card)
            # Закрываем буфер после отправки, чтобы освободить память
            buff.close()
            ava_buff.close()
            logger.info(
                f'Пользователь {get_info(interaction.user)} получил карточку со своим уровнем (уровень {lvl}, опыт {xp}/{required_xp})')
        except Exception as e:
            logger.error(
                f'Ошибка при генерации карточки уровня для пользователя {get_info(user)}: {e}', exc_info=True)
            embed = create_embed(
                title='Ошибка!',
                description=f'В процессе создания карточки с Вашим уровнем произошла ошибка. Пожалуйста, попробуйте позже',
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
            return

    class GuessModal(discord.ui.Modal, title='Угадай число'):
        """### Модальное окно для ввода числа в мини-игре "Угадай число"
        Получает от пользователя строку от 1 до 4 символов, валидирует её и 
        обрабатывает попытку угадать число, выдавая подсказки и обновляя состояние игры"""

        # Создаём текстовое поле для ввода числа
        guess_input = discord.ui.TextInput(
            label='Введите число от 1 до 1000',
            style=discord.TextStyle.short,
            placeholder='500',
            required=True,
            min_length=1,
            max_length=4,
        )

        def __init__(self, view: "General.GuessView"):
            super().__init__()
            # Сохраняем ссылку на View, чтобы обновлять его
            self.view = view

        async def on_submit(self, interaction: discord.Interaction):
            logger.info(
                f'Пользователь {get_info(interaction.user)} отправил число {self.guess_input.value} в игре "Угадай число"')
            # Проверяем, что пользователь ввёл число
            if not self.guess_input.value.isdigit():
                logger.warning(
                    f'{get_info(interaction.user)} ввёл некорректное значение в игре guess: {self.guess_input.value}')
                embed = create_embed(
                    title='Некорректный ввод!',
                    description='Пожалуйста, введите целое число от 1 до 1000',
                    color=discord.Color.red(),
                )
                await interaction.response.edit_message(embed=embed, view=self.view)
                return
            # Получаем данные для обработки ввода
            guess = int(self.guess_input.value)
            # Проверяем, что число в допустимых пределах
            if guess < 1 or guess > 1000:
                logger.warning(
                    f'{get_info(interaction.user)} ввёл число вне допустимого диапазона в игре guess: {guess}')
                embed = create_embed(
                    title='Некорректный ввод!',
                    description='Пожалуйста, введите целое число от 1 до 1000',
                    color=discord.Color.red(),
                )
                await interaction.response.edit_message(embed=embed, view=self.view)
                return
            pool = self.view.pool
            user_id = interaction.user.id
            guild_id = interaction.guild_id
            # Смотрим, угадал ли пользователь число
            # Если нет - выводим подсказку
            logger.debug(
                f'Обновляем данные об игре для пользователя {get_info(user_id)} в БД после попытки угадать число')
            try:
                async with pool.acquire() as connection:
                    # Увеличиваем счетчик попыток в БД
                    await connection.execute(
                        """
                        UPDATE guess_number 
                        SET tries = tries + 1 
                        WHERE guild_id = $1 AND user_id = $2
                    """,
                        guild_id,
                        user_id,
                    )

                    # Получаем актуальные данные
                    row = await pool.fetchrow(
                        """
                        SELECT number, tries 
                        FROM guess_number 
                        WHERE guild_id = $1 AND user_id = $2
                    """,
                        guild_id,
                        user_id,
                    )
                    target = row['number']
                    tries = row['tries']
                    logger.debug(
                        f'Обновлённые данные игры "Угадай число" для пользователя {get_info(user_id)} из БД: \
загаданное число {target}, кол-во попыток {tries}')
                    # Проверяем победу
                    if guess == target:
                        logger.debug(
                            f'Пользователь {get_info(user_id)} угадал загаданное число, удаляем данные о игре из БД и обрабатываем победу')
                        # Удаляем данные об игре из БД, так как она закончилась
                        await pool.execute(
                            """
                            DELETE FROM guess_number 
                            WHERE guild_id = $1 AND user_id = $2
                            """,
                            guild_id,
                            user_id,
                        )
                        embed = create_embed(
                            title='Вы угадали!',
                            description=f'Вы отгадали число **{target}** за **{tries}** попыток',
                            color=discord.Color.green(),
                        )
                        # Начисляем опыт за победу
                        if interaction.guild and guild_id:
                            # Вычисляем количество опыта, которое будет начислено пользователю
                            # Оно плавно убывает с каждой попыткой, но не может быть меньше 30
                            # И всегда оканчивается на 0
                            # (300 -> 180 -> 140 -> 110 -> 90 -> 80 -> 70.. -> 60.. -> 50.. -> 40.. -> 30..)
                            base = 300 // tries**0.75
                            xp = max((base + 9) // 10 * 10, 30)
                            if xp > 0:
                                await add_xp(user_id=user_id, guild_id=guild_id, xp=xp, pool=pool)
                            logger.info(
                                f'Пользователь {get_info(user_id)} угадал число {target} за {tries} попыток и получил {xp} XP')
                        self.view.stop()  # Останавливаем работу View
                        await interaction.response.edit_message(embed=embed, view=None)
                        return

                    # Создаём подсказку
                    current_direction = (
                        'Загаданное число больше'
                        if target > guess
                        else 'Загаданное число меньше'
                    )
                    # Отправляем подсказку пользователю
                    embed = create_embed(
                        title='Не угадали!',
                        description=f'{current_direction} чем **{guess}**',
                        color=discord.Color.orange(),
                        footer_text=f'Попыток: {tries}',
                    )
                    await interaction.response.edit_message(embed=embed, view=self.view)
                    logger.debug(
                        f'Пользователь {get_info(interaction.user)} сделал попытку угадать число {target}: \
{current_direction} чем {guess}. Попыток: {tries}')
            except Exception as e:
                logger.error(
                    f'Ошибка при обработке попытки в игре guess: {e}', exc_info=True
                )
                embed = create_embed(
                    title='Ошибка!',
                    description='Произошла ошибка при обработке введённого значения. Пожалуйста, попробуйте снова.',
                    color=discord.Color.red(),
                )
                await interaction.response.edit_message(embed=embed, view=self.view)

    class GuessView(discord.ui.View):
        """### UI-представление для мини-игры "Угадай число" 
        Содержит кнопку для открытия модального окна ввода числа"""

        def __init__(self, pool: asyncpg.Pool, interaction: discord.Interaction):
            super().__init__(timeout=300)
            self.pool = pool
            self.initial_interaction = interaction

        async def on_timeout(self):
            # По истечении тайм-аута пробуем удалить исходное сообщение
            try:
                logger.debug('Время игры "Угадай число" истекло')
                await self.initial_interaction.delete_original_response()
            except discord.NotFound:
                pass
            except Exception as e:
                logger.error(
                    f'Ошибка при удалении сообщения по тайм-ауту: {e}', exc_info=True
                )

        @discord.ui.button(
            label='Ввести число', style=discord.ButtonStyle.primary, emoji='1️⃣'
        )
        async def guess_button(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            # При нажатии открываем модальное окно для ввода числа
            await interaction.response.send_modal(General.GuessModal(self))

    @app_commands.command(name='guess', description='Мини-игра "Угадай число"')
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id, i.user.id))
    async def guess(self, interaction: discord.Interaction):
        """### Мини-игра "Угадай число". 
        Бот загадывает число от 1 до 1000, а пользователь пытается его угадать, получая подсказки "Больше" или "Меньше"

        Args:
            interaction (discord.Interaction): Объект взаимодействия, содержащий подробные данные об отправленной команде
        """
        if not isinstance(interaction.user, discord.Member):
            return
        logger.info(
            f'Пользователь {get_info(interaction.user)} вызвал команду /guess')
        pool = self.bot.db_pool
        if not pool:
            logger.warning(
                'Пул соединений с базой данных не инициализирован. Пропуск обработки команды guess'
            )
            embed = create_embed(
                title='Ошибка!',
                description='Произошла ошибка в процессе запуска игры. Пожалуйста, попробуйте позже',
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        user_id = interaction.user.id
        guild_id = interaction.guild_id
        try:
            async with pool.acquire() as connection:
                # Пробуем получить данные об активной игре пользователя
                row = await connection.fetchrow(
                    """
                    SELECT number, tries 
                    FROM guess_number 
                    WHERE guild_id = $1 AND user_id = $2
                """,
                    guild_id,
                    user_id,
                )
                # Если нет активной игры - создаём новую
                if not row:
                    target_number = randint(1, 1000)
                    await pool.execute(
                        """
                        INSERT INTO guess_number (guild_id, user_id, number, tries)
                        VALUES ($1, $2, $3, 0)
                        """,
                        guild_id,
                        user_id,
                        target_number,
                    )
                    message = (
                        'Загадано число от 1 до 1000. Попробуйте угадать ~(=^‥^)ノ'
                    )
                    tries = 0
                    # Логгируем данные для отладки и анализа
                    logger.debug(
                        f'Для пользователя {get_info(interaction.user)} была создана игра "Угадай число" с загаданным числом {target_number}')
                # Если игра уже есть - получаем нужные данные для продолжения
                # И создаём соответствующее сообщение
                else:
                    message = (
                        'У Вас уже есть загаданное число. Пробуйте угадывать дальше'
                    )
                    tries = row["tries"]
                    # Логгируем данные для отладки и анализа
                    logger.debug(
                        f'Пользователь {get_info(interaction.user)} продолжает игру "Угадай число". \
Загаданное число {row["number"]}, попыток {tries}')
                # Отправляем пользователю сообщение о начале игры с кнопкой для ввода числа
                embed = create_embed(
                    title='Попробуйте угадать число!',
                    description=message,
                    color=discord.Color.blue(),
                    footer_text=f'Текущих попыток: {tries}',
                )
                view = General.GuessView(pool, interaction)
                await interaction.response.send_message(
                    embed=embed, view=view, ephemeral=True
                )
        except Exception as e:
            logger.error(f'Ошибка при запуске игры guess: {e}', exc_info=True)
            embed = create_embed(
                title='Ошибка!',
                description='Произошла ошибка в процессе запуска игры. Пожалуйста, попробуйте позже',
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='rand', description='Генерирует случайное число в заданном диапазоне')
    @app_commands.describe(
        мин='Начало диапазона',
        макс='Конец диапазона'
    )
    @app_commands.checks.cooldown(1, 1.5, key=lambda i: (i.guild_id, i.user.id))
    async def rand(self, interaction: discord.Interaction, мин: int, макс: int):
        """### Команда для генерации случайного числа в заданном диапазоне.
        Пользователь вводит два числа - начало и конец диапазона, а бот генерирует число между ними и отправляет результат пользователю

        Args:
            interaction (discord.Interaction): Объект взаимодействия, содержащий подробные данные об отправленной команде
            мин (int): Начало диапазона для генерации случайного числа
            макс (int): Конец диапазона для генерации случайного числа
        """
        if not isinstance(interaction.user, discord.Member):
            return
        logger.info(
            f'Пользователь {get_info(interaction.user)} вызвал команду /rand для генерации случайного числа \
в диапазоне [{мин}; {макс}]')
        # Проверяем, если начало и конец диапазона совпадают
        if мин == макс:
            logger.warning(
                f'{get_info(interaction.user)} ввёл одинаковые числа для диапазона в команде rand [{мин}; {макс}]')
            embed = create_embed(
                title='Некорректный ввод!',
                description='Начало и конец диапазона не могут совпадать. Пожалуйста, введите разные числа.',
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        # Сохраняем значения в правильном порядке
        # И генерируем случайное число в заданном диапазоне
        start = min(мин, макс)
        end = max(мин, макс)
        result = randint(start, end)
        # Отправляем пользователю результат
        embed = create_embed(
            title='Сгенерировано случайное число!',
            description=f':game_die: **{result}** :game_die:',
            color=discord.Color.green(),
            footer_text=f'Диапазон: [{start}; {end}]',
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        # Логгируем данные для отладки и анализа
        logger.info(
            f'Для {get_info(interaction.user)} было сгенерировано число {result} в диапазоне [{start}; {end}]')

    class VoiceStatsView(discord.ui.View):
        def __init__(self, bot: "Bot", user: discord.User | discord.Member, initial_interaction: discord.Interaction):
            super().__init__(timeout=120.0)
            self.bot = bot
            self.user = user
            self.initial_interaction = initial_interaction
            self.current_period = 'week'
            self.mode = 'total'
            self.offset = 0
            self.update_buttons_state()

        async def on_timeout(self):
            try:
                logger.debug(
                    'Время работы интерфейса просмотра статистики времени "общения" истекло')
                await self.initial_interaction.delete_original_response()
            except Exception as e:
                logger.error(
                    f'Ошибка при удалении сообщения по тайм-ауту: {e}', exc_info=True
                )

        def update_buttons_state(self):
            self.next_period.disabled = (self.offset >= 0)

            self.set_week.style = discord.ButtonStyle.primary if self.current_period == 'week' else discord.ButtonStyle.secondary
            self.set_month.style = discord.ButtonStyle.primary if self.current_period == 'month' else discord.ButtonStyle.secondary
            self.set_year.style = discord.ButtonStyle.primary if self.current_period == 'year' else discord.ButtonStyle.secondary

            self.set_total.style = discord.ButtonStyle.primary if self.mode == 'total' else discord.ButtonStyle.secondary
            self.set_max_ssn.style = discord.ButtonStyle.primary if self.mode == 'max_ssn' else discord.ButtonStyle.secondary

        @discord.ui.button(emoji='⬅️', style=discord.ButtonStyle.secondary, row=0)
        async def prev_period(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.offset -= 1
            self.update_buttons_state()
            await self.update_stats(interaction)

        @discord.ui.button(label='-', style=discord.ButtonStyle.secondary, disabled=True, row=0)
        async def period_fill(self, interaction: discord.Interaction, button: discord.ui.Button):
            pass

        @discord.ui.button(emoji='➡️', style=discord.ButtonStyle.secondary, row=0)
        async def next_period(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.offset < 0:
                self.offset += 1
                self.update_buttons_state()
                await self.update_stats(interaction)

        @discord.ui.button(label='Неделя', style=discord.ButtonStyle.primary, row=1)
        async def set_week(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.current_period = 'week'
            self.offset = 0
            self.update_buttons_state()
            await self.update_stats(interaction)

        @discord.ui.button(label='Месяц', style=discord.ButtonStyle.secondary, row=1)
        async def set_month(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.current_period = 'month'
            self.offset = 0
            self.update_buttons_state()
            await self.update_stats(interaction)

        @discord.ui.button(label='Год', style=discord.ButtonStyle.secondary, row=1)
        async def set_year(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.current_period = 'year'
            self.offset = 0
            self.update_buttons_state()
            await self.update_stats(interaction)

        @discord.ui.button(label='Общее', style=discord.ButtonStyle.primary, row=2)
        async def set_total(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.mode = 'total'
            self.update_buttons_state()
            await self.update_stats(interaction)

        @discord.ui.button(label='-', style=discord.ButtonStyle.secondary, disabled=True, row=2)
        async def mode_fill(self, interaction: discord.Interaction, button: discord.ui.Button):
            pass

        @discord.ui.button(label='Сессии', style=discord.ButtonStyle.secondary, row=2)
        async def set_max_ssn(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.mode = 'max_ssn'
            self.update_buttons_state()
            await self.update_stats(interaction)

        async def get_stats_data(self):
            if not self.bot.db_pool:
                return [], [], "Ошибка Базы Данных"

            today = date.today()
            labels = []
            values = []
            range_text = ""

            async with self.bot.db_pool.acquire() as conn:
                tbl_name = 'voice_stats' if self.mode == 'total' else 'voice_max_sessions'
                val_name = 'seconds' if self.mode == 'total' else 'max_seconds'
                if self.current_period == "week":
                    start_of_week = today - \
                        timedelta(days=today.weekday()) + \
                        timedelta(weeks=self.offset)
                    end_of_week = start_of_week + timedelta(days=6)
                    range_text = f"{start_of_week.strftime('%d.%m.%Y')} — {end_of_week.strftime('%d.%m.%Y')}"
                    rows = await conn.fetch(
                        f"""
                        SELECT day, {val_name} FROM {tbl_name}
                        WHERE user_id = $1 AND guild_id = $2 AND day BETWEEN $3 AND $4
                        """,
                        self.user.id, self.initial_interaction.guild_id, start_of_week, end_of_week
                    )
                    db_data = {r['day']: r[val_name] for r in rows}

                    days_name = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
                    for i in range(7):
                        current_day = start_of_week + timedelta(days=i)
                        labels.append(days_name[i])
                        seconds = db_data.get(current_day, 0)
                        values.append(seconds)

                elif self.current_period == "month":
                    # Считаем месяц через общее число месяцев, чтобы смещение могло уходить на любое кол-во лет назад
                    current_year, month_idx = divmod(
                        today.year * 12 + today.month - 1 + self.offset, 12)
                    current_month = month_idx + 1

                    start_of_month = date(current_year, current_month, 1)
                    if current_month == 12:
                        end_of_month = date(current_year, 12, 31)
                    else:
                        end_of_month = date(
                            current_year, current_month + 1, 1) - timedelta(days=1)

                    range_text = f"{start_of_month.strftime('%d.%m.%Y')} — {end_of_month.strftime('%d.%m.%Y')}"
                    rows = await conn.fetch(
                        f"""
                        SELECT day, {val_name} FROM {tbl_name}
                        WHERE user_id = $1 AND guild_id = $2 AND day BETWEEN $3 AND $4
                        """,
                        self.user.id, self.initial_interaction.guild_id, start_of_month, end_of_month
                    )
                    db_data = {r['day']: r[val_name] for r in rows}

                    total_days = (end_of_month - start_of_month).days + 1
                    for i in range(total_days):
                        current_day = start_of_month + timedelta(days=i)
                        if (i + 1) in (1, 5, 10, 15, 20, 25, total_days):
                            labels.append(str(i + 1))
                        else:
                            labels.append("")
                        seconds = db_data.get(current_day, 0)
                        values.append(seconds)

                elif self.current_period == "year":
                    target_year = today.year + self.offset
                    start_of_year = date(target_year, 1, 1)
                    end_of_year = date(target_year, 12, 31)

                    range_text = f"{start_of_year.strftime('%d.%m.%Y')} — {end_of_year.strftime('%d.%m.%Y')}"

                    fnc_name = 'SUM' if self.mode == 'total' else 'MAX'
                    rows = await conn.fetch(
                        f"""
                        SELECT EXTRACT(MONTH FROM day) as month, {fnc_name}({val_name}) as total_seconds
                        FROM {tbl_name}
                        WHERE user_id = $1 AND guild_id = $2 AND day BETWEEN $3 AND $4
                        GROUP BY month
                        """,
                        self.user.id, self.initial_interaction.guild_id, start_of_year, end_of_year
                    )
                    db_data = {int(r['month']): r['total_seconds']
                               for r in rows}

                    months_abbrev = [
                        "Янв", "Фев", "Мар", "Апр", "Май", "Июн",
                        "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"
                    ]
                    for m in range(1, 13):
                        labels.append(months_abbrev[m - 1])
                        seconds = db_data.get(m, 0)
                        values.append(seconds)

            return labels, values, range_text

        def generate_plot(self, labels: list[str], values: list[float], title_range: str) -> io.BytesIO:
            logger.debug(
                f'Запущено построение графика {"времени общения" if self.mode == "total" else "максимальных сессий общения"}')
            fig, ax = plt.subplots(figsize=(7, 4.2), dpi=120)

            fig.patch.set_facecolor('#2f3136')

            ax.set_facecolor('#1e1f22')
            ax.grid(axis='y', linestyle='--', alpha=0.25, color='#ffffff')
            ax.tick_params(colors='#B9BBBE', labelsize=10)
            ax.set_ylabel('Время (в часах)', color='#DBDEE1',
                          fontsize=10, fontweight='bold')
            for spine in ax.spines.values():
                spine.set_visible(False)

            plt.title(title_range, color='#FFFFFF',
                      fontsize=12, fontweight='bold', pad=15)

            max_val = max(values) if values else 0

            if self.mode == 'total':
                if self.current_period in ("week", "month"):
                    x_positions = range(len(values))

                    plt.plot(x_positions, values, color='#5865F2', linewidth=2.5,
                             marker='o', markersize=6, markerfacecolor='#FFFFFF')

                    plt.fill_between(x_positions, values,
                                     color='#5865F2', alpha=0.15)

                    plt.xticks(x_positions, labels,
                               color='#B9BBBE', fontsize=10)

                elif self.current_period == "year":
                    x_positions = range(len(values))
                    plt.bar(x_positions, values, color='#5865F2', width=0.55,
                            edgecolor='#4752C4', linewidth=1, alpha=0.9, align='center')
                    plt.xticks(x_positions, labels,
                               color='#B9BBBE', fontsize=10)
            else:
                flt_values = []
                bar_colors = []

                for val in values:
                    flt_values.append(min(24, val))

                    if val == max_val and max_val > 0:
                        bar_colors.append('#FAA61A')
                    else:
                        bar_colors.append('#5865F2')

                x_positions = range(len(values))

                bars = plt.bar(x_positions, flt_values, color=bar_colors,
                               width=0.55, linewidth=1, alpha=0.9, align='center')

                plt.xticks(x_positions, labels, color='#B9BBBE', fontsize=10)
                for i, val in enumerate(values):
                    if val > 24:
                        plt.text(
                            x=x_positions[i],
                            y=24.5,
                            s=f"{val:.2f}ч",
                            ha='center',
                            va='bottom',
                            color='#FEE75C',
                            fontweight='bold',
                            fontsize=9
                        )

            if max_val <= 3:
                ax.yaxis.set_major_locator(ticker.MultipleLocator(0.25))
            elif max_val <= 6:
                ax.yaxis.set_major_locator(ticker.MultipleLocator(0.5))
            else:
                if max_val > 12:
                    ax.yaxis.set_major_locator(
                        ticker.MaxNLocator(integer=True, nbins=10))
                else:
                    ax.yaxis.set_major_locator(ticker.MultipleLocator(1.0))
            if max_val < 0.25:
                ax.set_ylim(0, 0.25)
            else:
                ax.set_ylim(bottom=0)

            logger.debug(
                'Построение графика завершено. Сохранение результата в буфер')
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=120,
                        bbox_inches='tight', facecolor=fig.get_facecolor())
            plt.close(fig)
            buf.seek(0)
            return buf

        async def update_stats(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            logger.debug(
                f'Получаем значения для текущего диапазона ({self.current_period}, {self.offset})')
            labels, values, range_text = await self.get_stats_data()
            conv_values = [round(value / 3600, 2) for value in values]
            logger.debug('Запускаем генерацию графика для полученных значений')
            buf = self.generate_plot(labels, conv_values, range_text)
            logger.debug('Сохраняем результат в файл для отправки')
            file = discord.File(buf, filename="stats_plot.png")
            logger.debug('Отправляем сообщение с полученным графиком')
            descr = None
            if self.mode == 'total':
                total = sum(values)
                descr = f'📅 **Период**: {range_text}\n🎤 **Время "общения"**: `{readable_time(total)}`'
            elif self.mode == 'max_ssn':
                total = max(values)
                descr = f'📅 **Период**: {range_text}\n🎤 **Максимальная сессия "общения"**: `{readable_time(total)}`'
            if descr is None:
                return
            embed = create_embed(
                title=f'Статистика общения — {self.user.display_name}',
                description=descr,
                image_url='attachment://stats_plot.png',
                color=discord.Color.blurple()
            )
            await interaction.edit_original_response(embed=embed, attachments=[file], view=self)
            buf.close()

    @app_commands.command(name='voice', description='Показывает Вашу статистику времени "общения" на сервере')
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id, i.user.id))
    async def voice(self, interaction: discord.Interaction, member: discord.Member | None = None):
        """### Команда для просмотра персональной статистики общения"""
        if not self.bot.db_pool or not isinstance(interaction.user, discord.Member):
            return

        logger.info(
            f'Пользователь {get_info(interaction.user)} вызвал команду /voice')
        target_member = member or interaction.user
        # Проверяем условия
        # - На сервере включен сбор статистики времени "общения"
        # - У пользователя не запрещён сбор статистики времени "общения"
        # - У пользователя не запрещён доступ к статистике для всех
        try:
            async with self.bot.db_pool.acquire() as con:
                guild_set = await con.fetchrow(
                    "SELECT vc_stats_enabled FROM guild_settings WHERE guild_id = $1",
                    interaction.guild_id
                )

                if not guild_set or not guild_set['vc_stats_enabled']:
                    embed = create_embed(
                        title='Ошибка!',
                        description='На данном сервере не разрешён сбор **статистики времени "общения"**',
                        color=discord.Color.red()
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return

                user_set = await con.fetchrow(
                    """
                    SELECT vc_stats_enabled, vc_stats_privacy 
                    FROM user_settings 
                    WHERE guild_id = $1 AND user_id = $2
                    """,
                    interaction.guild_id,
                    target_member.id
                )
                if not user_set:
                    await create_default_user_settings(self.bot.db_pool, target_member)
                    user_set = await con.fetchrow(
                        """
                        SELECT vc_stats_enabled, vc_stats_privacy 
                        FROM user_settings 
                        WHERE guild_id = $1 AND user_id = $2
                        """,
                        interaction.guild_id,
                        target_member.id
                    )
                is_enabled = user_set['vc_stats_enabled']
                privacy = user_set['vc_stats_privacy']
                if is_enabled is False:
                    embed = create_embed(
                        title='Ошибка!',
                        description=f'Выбранный пользователь запретил сбор **статистики времени "общения"**',
                        color=discord.Color.red()
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return
                if privacy is False and target_member.id != interaction.user.id:
                    embed = create_embed(
                        title='Ошибка!',
                        description=f'Выбранный пользователь запретил просмотр **статистики времени "общения"**',
                        color=discord.Color.red()
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return

        except Exception as e:
            logger.error(
                f'Ошибка в команде /voice для пользователя {get_info(target_member)}: {e}', exc_info=True)
            embed = create_embed(
                title='Произошла ошибка',
                description='Не удалось получить статистику времени общения. Попробуйте позже',
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        logger.debug('Создаём объект класса VoiceStatsView')
        view = self.VoiceStatsView(self.bot, target_member, interaction)
        logger.debug('Запускаем отрисовку графика')
        await view.update_stats(interaction)

    class VoiceDetailedSessionsView(discord.ui.View):
        """### UI-представление для просмотра детальных сессий "общения" по дням
        Содержит кнопки для изменения отображаемого периода"""

        def __init__(self, bot: 'Bot', user: discord.Member, initial_interaction: discord.Interaction):
            """### UI-представление для просмотра детальных сессий "общения" по дням
            Содержит кнопки для изменения отображаемого периода

            Args:
                bot (:class:`Bot`): Запущенный Дискорд-бот
                user (:class:`discord.Member`): Участник Дискорд-сервера
                initial_interaction (:class:`discord.Interaction`): Взаимодействие, создавшее данный интерфейс
            """
            super().__init__(timeout=180.0)
            self.bot = bot
            self.user = user
            self.day_offset = 0
            self.initial_interaction = initial_interaction
            self.collection_warning: str | None = None

        async def get_collection_warning(self) -> str | None:
            """### Функция для проверки настроек сбора детальных сессий "общения"
            Проверяет настройки сервера и пользователя, от которых зависит сохранение детальных сессий

            Returns:
                str | None: Причина, по которой сбор детальных сессий не ведётся, или None, если сбор ведётся
            """
            logger.debug(
                f'Проверка настроек сбора детальных сессий для пользователя {get_info(self.user)}')
            try:
                async with self.bot.db_pool.acquire() as con:
                    guild_set = await con.fetchrow(
                        "SELECT vc_stats_enabled FROM guild_settings WHERE guild_id = $1",
                        self.user.guild.id
                    )
                    user_set = await con.fetchrow(
                        """
                        SELECT vc_stats_enabled, vc_detailed_stats_enabled
                        FROM user_settings
                        WHERE guild_id = $1 AND user_id = $2
                        """,
                        self.user.guild.id,
                        self.user.id
                    )
            except Exception as e:
                logger.error(
                    f'Ошибка при проверке настроек сбора детальных сессий для пользователя {get_info(self.user)}: {e}', exc_info=True)
                return None
            if not guild_set or guild_set['vc_stats_enabled'] is not True:
                return 'На этом сервере отключен сбор **статистики времени "общения"**. Обратитесь к администрации сервера'
            if user_set and user_set['vc_stats_enabled'] is False:
                return 'Вы отключили **Сбор статистики времени "общения"**. Включить его можно в `/setup`'
            if not user_set or user_set['vc_detailed_stats_enabled'] is not True:
                return '**Сбор детальных сессий "общения"** отключен. Включить его можно в `/setup`'
            return None

        def build_ssn_embed(self, target_date: date, total_seconds: int) -> discord.Embed:
            """### Функция для создания Embed с детальными сессиями за день
            При отключенном сборе детальных сессий добавляет предупреждение с причиной

            Args:
                target_date (date): Отображаемый день
                total_seconds (int): Суммарное время "общения" за день в секундах

            Returns:
                discord.Embed: Embed с информацией о сессиях за день
            """
            readable_date = target_date.strftime('%d.%m.%Y')
            total_hours = total_seconds / 3600
            fields = None
            color = discord.Color.from_rgb(88, 101, 242)
            if self.collection_warning:
                fields = [{'name': '⚠️ Сбор сессий отключен',
                           'value': self.collection_warning, 'inline': False}]
                color = discord.Color.orange()
            embed = create_embed(
                title=f"Детализированные сессии — {self.user.display_name}",
                description=f"**Дата:** {readable_date}\n**Всего за день:** {total_hours:.2f} ч. ({readable_time(total_seconds)})",
                color=color,
                fields=fields
            )
            embed.set_image(url="attachment://daily_plot.png")
            return embed

        async def on_timeout(self):
            try:
                logger.debug(
                    'Время работы интерфейса просмотра детальных сессий "общения" истекло')
                await self.initial_interaction.delete_original_response()
            except Exception as e:
                logger.error(
                    f'Ошибка при удалении сообщения по тайм-ауту: {e}', exc_info=True
                )

        async def update_message(self, interaction: discord.Interaction):
            target_date = date.today() + timedelta(days=self.day_offset)

            sessions, total_seconds = await self.get_daily_sessions(target_date)

            plot_file = self.generate_daily_plot(
                target_date, sessions, self.collection_warning is not None)

            embed = self.build_ssn_embed(target_date, total_seconds)

            file = discord.File(plot_file, filename="daily_plot.png")

            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

        async def get_daily_sessions(self, target_date: date):
            logger.debug(
                f'Поиск подходящих сессий для {get_info(self.user)}. Запрашиваемая дата: {target_date}')
            start_of_day = datetime.combine(
                target_date, datetime.min.time(), tzinfo=MOSCOW_TZ)
            end_of_day = datetime.combine(
                target_date, datetime.max.time(), tzinfo=MOSCOW_TZ)

            async with self.bot.db_pool.acquire() as con:
                user_id = self.user.id
                guild_id = self.user.guild.id
                uid_hash, gid_hash = get_voice_hashes(user_id, guild_id)
                # Получаем сессии общения пользователя за выбранный день
                # Для этого конец сессии должен быть больше начала дня
                # А начало сессии - меньше конца дня
                rows = await con.fetch(
                    """
                    SELECT start_time, end_time
                    FROM voice_detailed_sessions
                    WHERE guild_id = $1 AND user_id = $2 AND start_time < $3 AND end_time > $4
                    ORDER BY start_time ASC
                    """,
                    gid_hash,
                    uid_hash,
                    end_of_day,
                    start_of_day
                )

            sessions = []
            total_seconds = 0

            for row in rows:
                st_local = row['start_time'].astimezone(MOSCOW_TZ)
                et_local = row['end_time'].astimezone(MOSCOW_TZ)

                sessions.append((st_local, et_local))
                if st_local < start_of_day:
                    st_local = start_of_day
                if et_local > end_of_day:
                    et_local = end_of_day
                logger.debug(
                    f'Найдена подходящая сессия: {st_local} - {et_local} ({int((et_local - st_local).total_seconds())} сек.)')
                total_seconds += (et_local - st_local).total_seconds()

            return sessions, int(total_seconds)

        def generate_daily_plot(self, target_date: date, sessions: list[tuple[datetime, datetime]], collection_disabled: bool = False) -> io.BytesIO:
            logger.debug(
                f'Отрисовка графика сессий пользователя {get_info(self.user)} за {target_date}')
            fig, ax = plt.subplots(figsize=(10, 3.5), dpi=120)

            fig.patch.set_facecolor('#2b2d31')
            ax.set_facecolor('#1e1f22')

            ax.grid(axis='x', which='both', linestyle='--',
                    alpha=0.15, color='#ffffff')

            for spine in ax.spines.values():
                spine.set_visible(False)

            ax.tick_params(colors='#dbdee1', labelsize=12)

            x_min = datetime.combine(target_date, datetime.min.time())
            x_max = datetime.combine(target_date, datetime.max.time())
            ax.set_xlim(x_min, x_max)

            import matplotlib.dates as mdates
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
            ax.xaxis.set_major_locator(
                mdates.HourLocator(interval=2))
            ax.xaxis.set_minor_locator(mdates.MinuteLocator(interval=30))

            ax.set_ylim(0, 2)
            ax.get_yaxis().set_visible(False)

            if not sessions:
                logger.debug(f'Сессии за {target_date} не найдены')
                ax.text(0.5, 0.5, 'Нет данных о сессиях за этот день',
                        color="#42bd41", ha='center', va='center', fontsize=24, transform=ax.transAxes)
            else:
                logger.debug(
                    f'Отрисовка {len(sessions)} {get_plural(len(sessions), ("сессии", "сессий", "сессий"))}')
                xranges = []
                for st, et in sessions:
                    st_naive = st.replace(tzinfo=None)
                    et_naive = et.replace(tzinfo=None)
                    st_fixed = x_min if st_naive < x_min else st_naive
                    et_fixed = x_max if et_naive > x_max else et_naive
                    logger.debug(f'Обработка сессии: {st_fixed} - {et_fixed}')

                    start_num = mdates.date2num(st_fixed)
                    duration_num = mdates.date2num(et_fixed) - start_num
                    xranges.append((start_num, duration_num))

                    if (et_fixed - st_fixed).total_seconds() > 2400:
                        center_time = st_fixed + (et_fixed - st_fixed) / 2
                        st_text = st_fixed.strftime(
                            '%H:%M') if st_naive == st_fixed else '-00:00'
                        et_text = et_fixed.strftime(
                            '%H:%M') if et_naive == et_fixed else '00:00+'
                        time_label = f"{st_text}\n{'–'*7}\n{et_text}"
                        logger.debug(
                            f'Длина сессии достаточна для добавления штампа времени: {st_text} - {et_text}')
                        ax.text(
                            mdates.date2num(center_time),
                            1,
                            time_label,
                            ha='center',
                            va='bottom',
                            color='#ffffff',
                            fontsize=10,
                            fontweight='bold',
                        )

                ax.broken_barh(xranges, (0.3, 1.4), facecolors='#5865F2',
                               edgecolor="#1e1f22", linewidth=1, alpha=0.9)

            data_stream = io.BytesIO()
            plt.savefig(data_stream, format='png', bbox_inches='tight',
                        facecolor=fig.get_facecolor(), dpi=120)
            plt.close(fig)
            data_stream.seek(0)
            return data_stream

        @discord.ui.button(label='', style=discord.ButtonStyle.primary, emoji='⬅️')
        async def prev_day(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.next_day.disabled = False
            if self.day_offset > -30:
                self.day_offset -= 1
            if self.day_offset == -30:
                self.prev_day.disabled = True
            await self.update_message(interaction)

        @discord.ui.button(label='Сегодня', style=discord.ButtonStyle.secondary)
        async def today_day(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.day_offset = 0
            await self.update_message(interaction)

        @discord.ui.button(label='', style=discord.ButtonStyle.primary, emoji='➡️')
        async def next_day(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.prev_day.disabled = False
            if self.day_offset < 0:
                self.day_offset += 1
            if self.day_offset == 0:
                self.next_day.disabled = True
            await self.update_message(interaction)

    @app_commands.command(name='sessions', description='Показывает Ваши сессии "общения"')
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 15.0, key=lambda i: (i.guild_id, i.user.id))
    async def sessions(self, interaction: discord.Interaction):
        """### Команда для просмотра детальной статистики общения"""
        logger.info(
            f'Пользователь {get_info(interaction.user)} вызвал команду /sessions')
        view = self.VoiceDetailedSessionsView(
            self.bot, interaction.user, interaction)

        # Проверяем настройки сбора один раз - результат используется и при переключении дней
        view.collection_warning = await view.get_collection_warning()
        if view.collection_warning:
            logger.debug(
                f'У пользователя {get_info(interaction.user)} не ведётся сбор детальных сессий - добавляем предупреждение')

        target_date = date.today()
        sessions, total_seconds = await view.get_daily_sessions(target_date)

        plot_file = view.generate_daily_plot(
            target_date, sessions, view.collection_warning is not None)

        embed = view.build_ssn_embed(target_date, total_seconds)

        file = discord.File(plot_file, filename="daily_plot.png")

        await interaction.response.send_message(embed=embed, file=file, view=view, ephemeral=True)

    @app_commands.command(name='privacy', description='Содержит Политику конфиденциальности, а также позволяет удалить Ваши данные')
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 60.0, key=lambda i: (i.guild_id, i.user.id))
    async def privacy(self, interaction: discord.Interaction):
        logger.info(
            f'Пользователь {get_info(interaction.user)} вызвал команду /privacy')
        embed = create_embed(
            title='Политика конфиденциальности',
            description='Slashy хранит Ваши данные, включая пользовательский ID и ID сервера Discord, чтобы обеспечить корректное функционирование'
            ' модулей, таких как Уровень активности, Мини-игры, Сессии "общения". Ниже приведена более подробная информация о хранении Ваших данных,'
            ' а также предоставлена возможность удаления части из них',
            color=discord.Color.yellow(),
            fields=[
                {'name': 'Хранятся в открытом виде (без псевдоанонимизации)',
                 'value': 'Уровень и опыт, игра "Угадай число" (только до завершения игры), настройки, '
                          'время "общения" по дням и максимальная сессия за день',
                 'inline': False},
                {'name': 'Детальные сессии',
                 'value': 'Только если Вы включили их в `/setup` и сессия длилась больше 5 минут. '
                          'ID пользователя и сервера хранятся в хэшированном виде, время начала '
                          'и окончания сессии - в открытом. '
                          'Удаляются через 30 дн. после начала.',
                 'inline': False},
                {'name': 'Сроки',
                 'value': 'Все данные, кроме детальных сессий "общения" хранятся бессрочно, пока Вы не удалите их вручную',
                 'inline': False},
                {'name': 'Логи',
                 'value': 'Хранятся до 30 дней, используются для отладки и сбора анонимной статистики. Удалить их через бота нельзя',
                 'inline': False},
                {'name': 'Управление',
                 'value': 'Отключить сбор и закрыть доступ к статистике можно, использовав `/setup`. '
                          'Для удаления Ваших данных, хранимых Slashy, Вы можете воспользоваться кнопками ниже',
                 'inline': False},
            ],
            timestamp=False,
        )
        await interaction.response.send_message(
            embed=embed, view=PrivacyView(self.bot, interaction), ephemeral=True)


async def setup(bot):
    await bot.add_cog(General(bot))
