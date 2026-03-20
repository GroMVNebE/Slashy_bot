import discord
from discord.ext import commands
import logging
from utils import add_xp
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from launch import Bot

logger = logging.getLogger('slashy.events')


class Events(commands.Cog):
    def __init__(self, bot: 'Bot'):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Обработчик отправки сообщения

            - Выдаёт пользователю опыт за отправку сообщений

        Args:
            message (discord.Message): Отправленное пользователем сообщение со всеми данными
        """
        # Пропускаем сообщения от ботов и в личных сообщениях
        if message.author.bot or not message.guild:
            return
        # Проверяем, что пул соединений с базой данных инициализирован
        if not self.bot.db_pool:
            logger.warning(
                "Пул соединений с базой данных не инициализирован. Пропуск обработки сообщения")
            return
        # Начисляем опыт за сообщение
        await add_xp(user_id=message.author.id, guild_id=message.guild.id, xp=10, pool=self.bot.db_pool)


async def setup(bot):
    await bot.add_cog(Events(bot))
