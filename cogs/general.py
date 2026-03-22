import discord
import logging
import os
from PIL import Image, ImageDraw, ImageFont
from discord import app_commands
from discord.ext import commands
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from launch import Bot

# Инициализируем логгер для этого модуля
logger = logging.getLogger('slashy.general')


class General(commands.Cog):
    """Модуль с общими командами"""

    def __init__(self, bot: 'Bot'):
        self.bot = bot
        self.exp_font = ImageFont.truetype("assets/the_weekend.otf", 24)
        self.lvl_font = ImageFont.truetype("assets/the_weekend.otf", 126)
        self.username_font = ImageFont.truetype("assets/the_weekend.otf", 36)

    @app_commands.command(
        name="lvl",
        description="Выводит Ваш текущий уровень"
    )
    async def lvl(self, interaction: discord.Interaction):
        """Выводит уровень и опыт пользователя, оформленный в embed-изображении

        Args:
            interaction (discord.Interaction): Объект взаимодействия, содержащий подробные данные об отправленной команде
        """
        # Проверяем, что пул соединений с базой данных инициализирован
        if not self.bot.db_pool:
            logger.warning(
                "Пул соединений с базой данных не инициализирован. Пропуск обработки сообщения")
            await interaction.response.send_message("Произошла ошибка.. Пожалуйста, попробуйте позже", ephemeral=True)
            return
        # Получаем данные пользователя
        user = interaction.user
        # Отправляем ответ, что бот обрабатывает запрос (может занять некоторое время)
        await interaction.response.defer(ephemeral=True)
        # Получаем данные об уровне и опыте пользователя из базы данных
        try:
            async with self.bot.db_pool.acquire() as connection:
                row = await connection.fetchrow("""
                    SELECT xp, level FROM user_levels
                    WHERE guild_id = $1 AND user_id = $2
                """, interaction.guild_id, user.id)
                if not row:
                    xp, lvl = 0, 1
                    await connection.execute("""
                        INSERT INTO user_levels (guild_id, user_id, xp, level)
                        VALUES ($1, $2, $3, $4)
                    """, interaction.guild_id, user.id, xp, lvl)
                else:
                    xp, lvl = row['xp'], row['level']
        except Exception as e:
            logger.error(
                f"Ошибка при получении данных об уровне из БД: {e}", exc_info=True)
            await interaction.followup.send("Произошла ошибка при получении данных о вашем уровне. Пожалуйста, попробуйте позже")
            return
        # Вычисляем прогресс до следующего уровня
        required_xp = 80*lvl + 20*lvl**2
        lvl_up_progress = xp / required_xp if required_xp > 0 else 0
        lvl_up_progress = min(lvl_up_progress, 1)  # Ограничиваем от 0 до 1
        try:
            # Загружаем шаблон карточки уровня
            bg = Image.open("assets/lvl_templ.png")
            # Получаем аватар пользователя, изменяем размер и вставляем на шаблон
            await user.display_avatar.save('ava.png')
            ava = Image.open('ava.png')
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
            draw.text((167, 170), name, font=self.username_font,
                      fill="#aed581", anchor="lm")
            # Вставляем уровень
            draw.text((194, 325), str(lvl), font=self.lvl_font,
                      fill="#aed581", anchor="mm")
            # Создаём круглый прогресс-бар, убирая лишнюю часть круга из шаблона
            draw.arc((335, 217, 530, 412), start=-90+int(360 *
                     lvl_up_progress), end=270, fill="#1d262a", width=36)
            # Вставляет опыт
            draw.text((432, 316), f"{xp}/{required_xp}",
                      font=self.exp_font, fill="#aed581", anchor="mm")
            # Сохраняем получившуюся карточку и отправляем пользователю
            bg.save('assets/lvl_card.png')
            card = discord.File('assets/lvl_card.png', filename='lvl_card.png')
            await interaction.followup.send(file=card)
            os.remove('ava.png')
            os.remove('assets/lvl_card.png')
        except Exception as e:
            logger.error(
                f"Ошибка при генерации карточки уровня: {e}", exc_info=True)
            await interaction.followup.send("Произошла ошибка при генерации карточки Вашего уровня. Пожалуйста, попробуйте позже")
            return


async def setup(bot):
    await bot.add_cog(General(bot))
