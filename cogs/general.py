import aiohttp
import asyncpg
import discord
import logging
import io
from PIL import Image, ImageDraw, ImageFont
from discord import app_commands
from discord.ext import commands
from typing import TYPE_CHECKING
from utils import create_embed, add_xp
from random import randint

if TYPE_CHECKING:
    from launch import Bot

# Инициализируем логгер для этого модуля
logger = logging.getLogger("slashy.general")


class General(commands.Cog):
    """Модуль с общими командами"""

    def __init__(self, bot: "Bot"):
        self.bot = bot
        self.exp_font = ImageFont.truetype("assets/the_weekend.otf", 24)
        self.lvl_font = ImageFont.truetype("assets/the_weekend.otf", 126)
        self.username_font = ImageFont.truetype("assets/the_weekend.otf", 36)

    @app_commands.command(name="lvl", description="Выводит Ваш текущий уровень")
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id, i.user.id))
    async def lvl(self, interaction: discord.Interaction):
        """### Выводит данные об уровне и опыте пользователя
        Выводит карточку с аватаром, никнеймом, уровнем, опытом и прогрессом до следующего уровня пользователя,
        вызвавшего команду

        Args:
            interaction (discord.Interaction): Объект взаимодействия, содержащий подробные данные об отправленной команде
        """
        # Проверяем, что пул соединений с базой данных инициализирован
        if not self.bot.db_pool:
            logger.warning(
                "Пул соединений с базой данных не инициализирован. Пропуск обработки сообщения"
            )
            embed = create_embed(
                title="Ошибка!",
                description=f"В процессе создания карточки с Вашим уровнем произошла ошибка. Пожалуйста, попробуйте позже",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )
            return
        # Получаем данные пользователя
        user = interaction.user
        # Отправляем ответ, что бот обрабатывает запрос (может занять некоторое время)
        await interaction.response.defer(ephemeral=True)
        # Получаем данные об уровне и опыте пользователя из базы данных
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
                    xp, lvl = row["xp"], row["level"]
        except Exception as e:
            logger.error(
                f"Ошибка при получении данных об уровне из БД: {e}", exc_info=True
            )
            embed = create_embed(
                title="Ошибка!",
                description=f"В процессе создания карточки с Вашим уровнем произошла ошибка. Пожалуйста, попробуйте позже",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
            return
        # Вычисляем прогресс до следующего уровня
        required_xp = 80 * lvl + 20 * lvl**2
        lvl_up_progress = xp / required_xp if required_xp > 0 else 0
        lvl_up_progress = min(lvl_up_progress, 1)  # Ограничиваем от 0 до 1
        try:
            # Загружаем шаблон карточки уровня
            bg = Image.open("assets/lvl_templ.png")
            # Получаем аватар пользователя, изменяем размер и вставляем на шаблон
            url = user.display_avatar.url
            ava = None
            ava_buff = None
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.read()
                        ava_buff = io.BytesIO(data)
                        ava = Image.open(ava_buff).convert("RGBA")
            if ava is None or ava_buff is None:
                logger.error(
                    f"Не удалось загрузить аватар пользователя {user.id}", exc_info=True
                )
                embed = create_embed(
                    title="Ошибка!",
                    description=f"В процессе создания карточки с Вашим уровнем произошла ошибка. Пожалуйста, попробуйте позже",
                    color=discord.Color.red(),
                )
                await interaction.followup.send(embed=embed)
                return
            ava = ava.resize(size=[65, 65])
            bg.paste(ava, (90, 138))
            # Получаем имя пользователя и обрезаем его, если оно слишком длинное для отображения на карточке
            name = user.display_name
            while self.username_font.getlength(name) > 325:
                name = name[:-1]
            if name != user.display_name:
                name = name + "..."
            # Наачинаем изменение шаблона
            draw = ImageDraw.Draw(bg)
            # Вставляем на карточку
            draw.text(
                (167, 170), name, font=self.username_font, fill="#aed581", anchor="lm"
            )
            # Вставляем уровень
            draw.text(
                (194, 325), str(lvl), font=self.lvl_font, fill="#aed581", anchor="mm"
            )
            # Создаём круглый прогресс-бар, убирая лишнюю часть круга из шаблона
            draw.arc(
                (335, 217, 530, 412),
                start=-90 + int(360 * lvl_up_progress),
                end=270,
                fill="#1d262a",
                width=36,
            )
            # Вставляет опыт
            draw.text(
                (432, 316),
                f"{xp}/{required_xp}",
                font=self.exp_font,
                fill="#aed581",
                anchor="mm",
            )
            # Сохраняем получившуюся карточку
            buff = io.BytesIO()
            bg.save(buff, format="PNG")
            buff.seek(0)
            card = discord.File(buff, filename=f"lvl_card_{user.id}.png")
            # Добавляем карточку в Embed
            embed = create_embed(
                title=f"Данные об уровне {user.display_name}",
                image_url=f"attachment://lvl_card_{user.id}.png",
            )
            # Отправляем Embed с карточкой пользователю
            await interaction.followup.send(embed=embed, file=card)
            # Закрываем буфер после отправки, чтобы освободить память
            buff.close()
            ava_buff.close()
        except Exception as e:
            logger.error(
                f"Ошибка при генерации карточки уровня: {e}", exc_info=True)
            embed = create_embed(
                title="Ошибка!",
                description=f"В процессе создания карточки с Вашим уровнем произошла ошибка. Пожалуйста, попробуйте позже",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
            return

    class GuessModal(discord.ui.Modal, title="Угадай число"):
        """### Модальное окно для ввода числа в мини-игре "Угадай число"
        Получает от пользователя строку от 1 до 4 символов, валидирует её и 
        обрабатывает попытку угадать число, выдавая подсказки и обновляя состояние игры"""

        # Создаём текстовое поле для ввода числа
        guess_input = discord.ui.TextInput(
            label="Введите число от 1 до 1000",
            style=discord.TextStyle.short,
            placeholder="500",
            required=True,
            min_length=1,
            max_length=4,
        )

        def __init__(self, view: "General.GuessView"):
            super().__init__()
            # Сохраняем ссылку на View, чтобы обновлять его
            self.view = view

        async def on_submit(self, interaction: discord.Interaction):
            # Проверяем, что пользователь ввёл число
            if not self.guess_input.value.isdigit():
                embed = create_embed(
                    title="Некорректный ввод!",
                    description="Пожалуйста, введите целое число от 1 до 1000",
                    color=discord.Color.red(),
                )
                await interaction.response.edit_message(embed=embed, view=self.view)
                return
            # Получаем данные для обработки ввода
            guess = int(self.guess_input.value)
            # Проверяем, что число в допустимых пределах
            if guess < 1 or guess > 1000:
                embed = create_embed(
                    title="Некорректный ввод!",
                    description="Пожалуйста, введите целое число от 1 до 1000",
                    color=discord.Color.red(),
                )
                await interaction.response.edit_message(embed=embed, view=self.view)
                return
            pool = self.view.pool
            user_id = interaction.user.id
            guild_id = interaction.guild_id
            # Смотрим, угадал ли пользователь число
            # Если нет - выводим подсказку
            try:
                async with pool.acquire() as connection:
                    # Увеличиваем счетчик попыток в БД
                    await connection.execute(
                        "UPDATE guess_number SET tries = tries + 1 WHERE guild_id = $1 AND user_id = $2",
                        guild_id,
                        user_id,
                    )

                    # Получаем актуальные данные
                    row = await pool.fetchrow(
                        "SELECT number, tries FROM guess_number WHERE guild_id = $1 AND user_id = $2",
                        guild_id,
                        user_id,
                    )
                    target = row["number"]
                    tries = row["tries"]

                    # Проверяем победу
                    if guess == target:
                        # Удаляем данные об игре из БД, так как она закончилась
                        await pool.execute(
                            "DELETE FROM guess_number WHERE guild_id = $1 AND user_id = $2",
                            guild_id,
                            user_id,
                        )
                        embed = create_embed(
                            title="Вы угадали!",
                            description=f"Вы отгадали число **{target}** за **{tries}** попыток",
                            color=discord.Color.green(),
                        )
                        # Начисляем опыт за победу
                        if interaction.guild:
                            # Вычисляем количество опыта, которое будет начислено пользователю
                            # Оно плавно убывает с каждой попыткой, но не может быть меньше 30
                            # И всегда оканчивается на 0
                            # (300 -> 180 -> 140 -> 110 -> 90 -> 80 -> 70.. -> 60.. -> 50.. -> 40.. -> 30..)
                            base = 300 // tries**0.75
                            xp = max((base + 9) // 10 * 10, 30)
                            await add_xp(user_id=interaction.user.id, guild_id=interaction.guild.id, xp=xp, pool=pool)
                        self.view.stop()  # Останавливаем работу View
                        await interaction.response.edit_message(embed=embed, view=None)
                        return

                    # Создаём подсказку
                    current_direction = (
                        "Загаданное число больше"
                        if target > guess
                        else "Загаданное число меньше"
                    )
                    # Отправляем подсказку пользователю
                    embed = create_embed(
                        title="Не угадали!",
                        description=f"{current_direction} чем **{guess}**",
                        color=discord.Color.orange(),
                        footer_text=f"Попыток: {tries}",
                    )
                    await interaction.response.edit_message(embed=embed, view=self.view)
            except Exception as e:
                logger.error(
                    f"Ошибка при обработке попытки в игре guess: {e}", exc_info=True
                )
                embed = create_embed(
                    title="Ошибка!",
                    description="Произошла ошибка при обработке введённого значения. Пожалуйста, попробуйте снова.",
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
                await self.initial_interaction.delete_original_response()
            except discord.NotFound:
                pass
            except Exception as e:
                logger.error(
                    f"Ошибка при удалении сообщения по тайм-ауту: {e}", exc_info=True
                )

        @discord.ui.button(
            label="Ввести число", style=discord.ButtonStyle.primary, emoji="1️⃣"
        )
        async def guess_button(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            # При нажатии открываем модальное окно для ввода числа
            await interaction.response.send_modal(General.GuessModal(self))

    @app_commands.command(name="guess", description='Мини-игра "Угадай число"')
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id, i.user.id))
    async def guess(self, interaction: discord.Interaction):
        """### Мини-игра "Угадай число". 
        Бот загадывает число от 1 до 1000, а пользователь пытается его угадать, получая подсказки "Больше" или "Меньше"

        Args:
            interaction (discord.Interaction): Объект взаимодействия, содержащий подробные данные об отправленной команде
        """
        pool = self.bot.db_pool
        if not pool:
            logger.warning(
                "Пул соединений с базой данных не инициализирован. Пропуск обработки команды guess"
            )
            embed = create_embed(
                title="Ошибка!",
                description="Произошла ошибка в процессе запуска игры. Пожалуйста, попробуйте позже",
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
                    "SELECT number FROM guess_number WHERE guild_id = $1 AND user_id = $2",
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
                        "Загадано число от 1 до 1000. Попробуйте угадать ~(=^‥^)ノ"
                    )
                    tries = 0
                # Если игра уже есть - получаем нужные данные для продолжения
                # И создаём соответствующее сообщение
                else:
                    message = (
                        "У Вас уже есть загаданное число. Пробуйте угадывать дальше"
                    )
                    tries = row["tries"]

                # Отправляем пользователю сообщение о начале игры с кнопкой для ввода числа
                embed = create_embed(
                    title="Попробуйте угадать число!",
                    description=message,
                    color=discord.Color.blue(),
                    footer_text=f"Текущих попыток: {tries}",
                )
                view = General.GuessView(pool, interaction)
                await interaction.response.send_message(
                    embed=embed, view=view, ephemeral=True
                )
        except Exception as e:
            logger.error(f"Ошибка при запуске игры guess: {e}", exc_info=True)
            embed = create_embed(
                title="Ошибка!",
                description="Произошла ошибка в процессе запуска игры. Пожалуйста, попробуйте позже",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="rand", description="Генерирует случайное число в заданном диапазоне")
    @app_commands.describe(
        мин="Начало диапазона",
        макс="Конец диапазона"
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
        # Проверяем, если начало и конец диапазона совпадают
        if мин == макс:
            embed = create_embed(
                title="Некорректный ввод!",
                description="Начало и конец диапазона не могут совпадать. Пожалуйста, введите разные числа.",
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
            title="Сгенерировано случайное число!",
            description=f":game_die:    **{result}**    :game_die:",
            color=discord.Color.green(),
            footer_text=f"Диапазон: [{start}; {end}]",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(General(bot))
