import discord
import logging
from discord import app_commands
from discord.ext import commands

# Инициализируем логгер для этого модуля
logger = logging.getLogger('bot.general')


class General(commands.Cog):
    """Модуль с общими командами"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="hello",
        description="Выводит приветственное сообщение"
    )
    async def hello(self, interaction: discord.Interaction):
        """Отправляет приветственное сообщение пользователю, который вызвал команду

        Args:
            interaction (discord.Interaction): Объект взаимодействия, содержащий подробные данные об отправленной команде
        """
        # Получаем данные пользователя
        user = interaction.user
        # Сохраняем лог об использовании команды
        logger.info(f"Пользователь {user} поздоровался с Slashy")
        # Отправляем ответ
        await interaction.response.send_message(f"Привет, {user.mention} (oﾟvﾟ)ノ")


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
