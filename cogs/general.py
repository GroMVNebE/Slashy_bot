import matplotlib.ticker as ticker
import matplotlib.pyplot as plt
import aiohttp
import asyncpg
import discord
import logging
import io
from datetime import timedelta, date
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

# Инициализируем логгер для этого модуля
logger = logging.getLogger('slashy.general')
logging.getLogger('matplotlib').setLevel(logging.WARNING)


class General(commands.Cog):
    """### Модуль с общими командами

    - Команда :meth:`lvl` для вывода карточки с уровнем пользователя
    - Команда :meth:`guess` для игры "Угадай число"
    - Команда :meth:`rand` для генерации случайного числа в заданном диапазоне
    - Команда :meth:`voice` для вывода статистики времени \"общения\""""

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
            f'Пользователь {interaction.user.id} ({interaction.user.display_name}) запросил карточку со своим уровнем')
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
            f'Получение данных об уровне пользователя {user.id} ({user.display_name}) на сервере {interaction.guild_id} ({interaction.guild.name if interaction.guild else None}) из БД')
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
                f'Получение данных об уровне пользователя {user.id} ({user.display_name}) на сервере {interaction.guild_id} ({interaction.guild.name if interaction.guild else None}) из БД: Успешно')
        except Exception as e:
            logger.error(
                f'Ошибка при получении данных об уровне пользователя {user.id} ({user.display_name}) на сервере {interaction.guild_id} ({interaction.guild.name if interaction.guild else None}) из БД: {e}', exc_info=True
            )
            embed = create_embed(
                title='Ошибка!',
                description=f'В процессе создания карточки с Вашим уровнем произошла ошибка. Пожалуйста, попробуйте позже',
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
            return
        # Вычисляем прогресс до следующего уровня
        required_xp = 80 * lvl + 20 * lvl**2
        lvl_up_progress = xp / required_xp if required_xp > 0 else 0
        lvl_up_progress = min(lvl_up_progress, 1)  # Ограничиваем от 0 до 1
        logger.debug(
            f'Создание карточки уровня для пользователя {user.id} ({user.display_name}) на сервере {interaction.guild_id} ({interaction.guild.name if interaction.guild else None})')
        try:
            # Загружаем шаблон карточки уровня
            bg = Image.open('assets/lvl_templ.png')
            # Получаем аватар пользователя, изменяем размер и вставляем на шаблон
            url = user.display_avatar.url
            ava = None
            ava_buff = None
            logger.debug(
                f'Загрузка аватара пользователя {user.id} ({user.display_name}) для карточки уровня')
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.read()
                        ava_buff = io.BytesIO(data)
                        ava = Image.open(ava_buff).convert('RGBA')
            if ava is None or ava_buff is None:
                logger.error(
                    f'Не удалось загрузить аватар пользователя {user.id} ({user.display_name})', exc_info=True
                )
                embed = create_embed(
                    title='Ошибка!',
                    description=f'В процессе создания карточки с Вашим уровнем произошла ошибка. Пожалуйста, попробуйте позже',
                    color=discord.Color.red(),
                )
                await interaction.followup.send(embed=embed)
                return
            logger.debug(
                f'Вставляем данные пользователя {user.id} ({user.display_name}) на шаблон карточки')
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
                f'Сохраняем карточку уровня в буфер для отправки пользователю {user.id} ({user.display_name})')
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
                f'Пользователь {interaction.user.id} ({interaction.user.display_name}) получил карточку со своим уровнем (уровень {lvl}, опыт {xp}/{required_xp})')
        except Exception as e:
            logger.error(
                f'Ошибка при генерации карточки уровня для пользователя {user.id} ({user.display_name}): {e}', exc_info=True)
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
                f'Пользователь {interaction.user.id} отправил число {self.guess_input.value} в игре "Угадай число"')
            # Проверяем, что пользователь ввёл число
            if not self.guess_input.value.isdigit():
                logger.warning(
                    f'{interaction.user.id} ввёл некорректное значение в игре guess: {self.guess_input.value}')
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
                    f'{interaction.user.id} ввёл число вне допустимого диапазона в игре guess: {guess}')
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
                f'Обновляем данные об игре для пользователя {user_id} в БД после попытки угадать число')
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
                        f'Обновлённые данные игры "Угадай число" для пользователя {user_id} из БД: \
загаданное число {target}, кол-во попыток {tries}')
                    # Проверяем победу
                    if guess == target:
                        logger.debug(
                            f'Пользователь {user_id} угадал загаданное число, удаляем данные о игре из БД и обрабатываем победу')
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
                            await add_xp(user_id=user_id, guild_id=guild_id, xp=xp, pool=pool)
                            logger.info(
                                f'Пользователь {user_id} угадал число {target} за {tries} попыток и получил {xp} XP')
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
                        f'Пользователь {interaction.user.display_name} сделал попытку угадать число {target}: \
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
        if not type(interaction.user) is discord.Member:
            return
        logger.info(
            f'Пользователь {user_data(interaction.user)} использовал команду /guess')
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
                        f'Для пользователя {interaction.user.display_name} была создана игра "Угадай число" с загаданным числом {target_number}')
                # Если игра уже есть - получаем нужные данные для продолжения
                # И создаём соответствующее сообщение
                else:
                    message = (
                        'У Вас уже есть загаданное число. Пробуйте угадывать дальше'
                    )
                    tries = row["tries"]
                    # Логгируем данные для отладки и анализа
                    logger.debug(
                        f'Пользователь {interaction.user.display_name} продолжает игру "Угадай число". \
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
        if not type(interaction.user) is discord.Member:
            return
        logger.info(
            f'Пользователь {user_data(interaction.user)} запросил генерацию случайного числа \
в диапазоне [{мин}; {макс}]')
        # Проверяем, если начало и конец диапазона совпадают
        if мин == макс:
            logger.warning(
                f'{interaction.user.display_name} ввёл одинаковые числа для диапазона в команде rand [{мин}; {макс}]')
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
            f'Для {interaction.user.display_name} было сгенерировано число {result} в диапазоне [{start}; {end}]')

    class VoiceStatsView(discord.ui.View):
        def __init__(self, bot: "Bot", user: discord.User | discord.Member, initial_interaction: discord.Interaction):
            super().__init__(timeout=120.0)
            self.bot = bot
            self.user = user
            self.initial_interaction = initial_interaction
            self.current_period = "week"
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

            self.set_week.style = discord.ButtonStyle.primary if self.current_period == "week" else discord.ButtonStyle.secondary
            self.set_month.style = discord.ButtonStyle.primary if self.current_period == "month" else discord.ButtonStyle.secondary
            self.set_year.style = discord.ButtonStyle.primary if self.current_period == "year" else discord.ButtonStyle.secondary

        @discord.ui.button(emoji='⬅️', style=discord.ButtonStyle.secondary, row=0)
        async def prev_period(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.offset -= 1
            self.update_buttons_state()
            await self.update_stats(interaction)

        @discord.ui.button(style=discord.ButtonStyle.secondary, disabled=True, row=0)
        async def current_label(self, interaction: discord.Interaction, button: discord.ui.Button):
            pass

        @discord.ui.button(emoji='➡️', style=discord.ButtonStyle.secondary, row=0)
        async def next_period(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.offset < 0:
                self.offset += 1
                self.update_buttons_state()
                await self.update_stats(interaction)

        @discord.ui.button(label="Неделя", style=discord.ButtonStyle.primary, row=1)
        async def set_week(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.current_period = "week"
            self.offset = 0
            self.update_buttons_state()
            await self.update_stats(interaction)

        @discord.ui.button(label="Месяц", style=discord.ButtonStyle.secondary, row=1)
        async def set_month(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.current_period = "month"
            self.offset = 0
            self.update_buttons_state()
            await self.update_stats(interaction)

        @discord.ui.button(label="Год", style=discord.ButtonStyle.secondary, row=1)
        async def set_year(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.current_period = "year"
            self.offset = 0
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
                if self.current_period == "week":
                    start_of_week = today - \
                        timedelta(days=today.weekday()) + \
                        timedelta(weeks=self.offset)
                    end_of_week = start_of_week + timedelta(days=6)
                    range_text = f"{start_of_week.strftime('%d.%m.%Y')} — {end_of_week.strftime('%d.%m.%Y')}"
                    self.current_label.label = range_text
                    rows = await conn.fetch(
                        """
                        SELECT day, seconds FROM voice_stats
                        WHERE user_id = $1 AND guild_id = $2 AND day BETWEEN $3 AND $4
                        """,
                        self.user.id, self.initial_interaction.guild_id, start_of_week, end_of_week
                    )
                    db_data = {r['day']: r['seconds'] for r in rows}

                    days_name = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
                    for i in range(7):
                        current_day = start_of_week + timedelta(days=i)
                        labels.append(days_name[i])
                        seconds = db_data.get(current_day, 0)
                        values.append(seconds)

                elif self.current_period == "month":
                    current_year = today.year
                    current_month = today.month + self.offset

                    if current_month <= 0:
                        current_year -= 1
                        current_month += 12

                    start_of_month = date(current_year, current_month, 1)
                    if current_month == 12:
                        end_of_month = date(current_year, 12, 31)
                    else:
                        end_of_month = date(
                            current_year, current_month + 1, 1) - timedelta(days=1)

                    range_text = f"{start_of_month.strftime('%d.%m.%Y')} — {end_of_month.strftime('%d.%m.%Y')}"
                    self.current_label.label = range_text
                    rows = await conn.fetch(
                        """
                        SELECT day, seconds FROM voice_stats
                        WHERE user_id = $1 AND guild_id = $2 AND day BETWEEN $3 AND $4
                        """,
                        self.user.id, self.initial_interaction.guild_id, start_of_month, end_of_month
                    )
                    db_data = {r['day']: r['seconds'] for r in rows}

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
                    self.current_label.label = range_text

                    rows = await conn.fetch(
                        """
                        SELECT EXTRACT(MONTH FROM day) as month, SUM(seconds) as total_seconds
                        FROM voice_stats
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
            plt.clf()
            logger.debug('Строим график')
            plt.figure(figsize=(7, 4.2), facecolor='#2f3136')
            ax = plt.axes()
            ax.set_facecolor('#2f3136')

            for spine in ax.spines.values():
                spine.set_visible(False)

            if self.current_period in ("week", "month"):
                x_positions = range(len(values))

                plt.plot(x_positions, values, color='#5865F2', linewidth=2.5,
                         marker='o', markersize=6, markerfacecolor='#FFFFFF')

                plt.fill_between(x_positions, values,
                                 color='#5865F2', alpha=0.15)

                plt.xticks(x_positions, labels, color='#B9BBBE', fontsize=10)

            elif self.current_period == "year":
                x_positions = range(len(values))
                plt.bar(x_positions, values, color='#5865F2', width=0.55,
                        edgecolor='#4752C4', linewidth=1, alpha=0.9, align='center')
                plt.xticks(x_positions, labels, color='#B9BBBE', fontsize=10)

            plt.yticks(color='#B9BBBE', fontsize=10)
            ax.yaxis.grid(True, linestyle='--', alpha=0.15, color='#FFFFFF')
            ax.xaxis.grid(False)

            max_val = max(values) if values else 0
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

            ax.tick_params(axis='y', colors='#B9BBBE', labelsize=10)
            ax.yaxis.grid(True, linestyle='--', alpha=0.15, color='#FFFFFF')
            ax.xaxis.grid(False)

            if max_val < 0.25:
                ax.set_ylim(0, 0.25)
            else:
                ax.set_ylim(bottom=0)

            plt.title(title_range, color='#FFFFFF',
                      fontsize=12, fontweight='bold', pad=15)
            plt.ylabel('Время общения (в часах)',
                       color='#B9BBBE', fontsize=10, labelpad=10)
            logger.debug('Сохраняем результат в буфер')
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=110,
                        bbox_inches='tight', facecolor='#2f3136')
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
            total = sum(values)
            embed = create_embed(
                title=f'Статистика общения — {self.user.display_name}',
                description=f'📅 **Период**: {range_text}\n🎤 **Время "общения"**: `{readable_time(total)}`',
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
        if not self.bot.db_pool or not type(interaction.user) is discord.Member:
            return

        logger.info(
            f'Пользователь {user_data(interaction.user)} вызвал команду /voice')
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
                    await create_default_user_settings(self.bot, target_member)
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
                f'Ошибка в команде /voice для пользователя {target_member.id}: {e}', exc_info=True)
            embed = create_embed(
                title='Произошла ошибка',
                description='Не удалось получить статистику времени общения. Попробуйте позже',
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        logger.debug('Создаём объект класса VoiceStatsView')
        view = self.VoiceStatsView(self.bot, target_member, interaction)
        logger.debug('Запускаем отрисовку графика')
        await view.update_stats(interaction)


async def setup(bot):
    await bot.add_cog(General(bot))
