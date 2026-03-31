import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import TYPE_CHECKING
from launch import Bot
from utils import create_embed, get_env

# Подлючаем типизацию для класса Bot из launch.py, избегая циклического импорта
# Нужно для правильного определения типов в IDE
if TYPE_CHECKING:
    from launch import Bot

# Инициализируем логгер для этого модуля
logger = logging.getLogger("slashy.admin")

# Получаем ID разработчика из окружения для проверки прав
CREATOR_ID = int(get_env("CREATOR_ID"))


class ModuleSelect(discord.ui.Select):
    """### Выпадающий список для выбора модуля на перезагрузку
    Содержит варианты выбора модулей для перезагрузки,
    после выбора перезагружает модуль и синхронизирует команды"""

    def __init__(self, bot: "Bot"):
        self.bot: Bot = bot
        # Добавляем варианты выбора модулей для перезагрузки
        # value должен соответствовать формату "cog_name" где "cogs/cog_name.py" - файл кога
        # пр. "general" для "cogs/general.py"
        options = [
            discord.SelectOption(
                label="Общие команды", description="Перезагрузить модуль с общими командами (cogs/general.py)", value="general"),
        ]
        super().__init__(placeholder="Выберите модуль для перезагрузки...",
                         min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Получаем имя выбранного модуля и формируем путь к нему
        module_name = self.values[0]
        cog_path = f"cogs.{module_name}"
        logger.info(f'Была начата перезагрузка модуля {module_name}')
        # Отправляем ответ, что бот обрабатывает запрос (может занять некоторое время)
        await interaction.response.defer(ephemeral=True)
        # Пробуем перезагрузить выбранный модуль и синхронизировать команды
        try:
            # Перезагружаем модуль
            await self.bot.reload_extension(cog_path)
            # Синхронизируем команды после перезагрузки
            # (На случай, если были добавлены/удалены команды или изменены их параметры)
            await self.bot.tree.sync()
            # Отправляем пользователю сообщение об успешной перезагрузке
            embed = create_embed(
                title="Перезагрузка модуля завершена",
                description=f"Модуль **{module_name}** был успешно перезагружен. В случае, если были добавлены/удалены команды, или изменены их параметры, изменения должны вступить в силу через некоторое время",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            # Логируем успешную перезагрузку
            logger.info(f"Модуль {cog_path} перезагружен")
        # Если произошла ошибка при перезагрузке, логируем её и отправляем пользователю сообщение об ошибке
        except Exception as e:
            logger.error(
                f"Ошибка перезагрузки модуля {cog_path}: {e}", exc_info=True)
            embed = create_embed(
                title="Прозошла ошибка",
                description=f"В процессе перезагрузки модуля **{module_name}** произошла ошибка. Проверьте логи для получения подробной информации",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


class ModuleReloadView(discord.ui.View):
    """### UI-представление для выбора модуля на перезагрузку
    Содержит в себе выпадающий список :class:`ModuleSelect` для выбора модуля на перезагрузку"""

    def __init__(self, bot: "Bot", interaction: discord.Interaction):
        super().__init__(timeout=120)
        self.add_item(ModuleSelect(bot))
        self.initial_interaction = interaction

    async def on_timeout(self):
        # По истечении тайм-аута пробуем удалить исходное сообщение
        try:
            logger.info("Время работы панели выбора модуля истекло")
            await self.initial_interaction.delete_original_response()
        except discord.NotFound:
            pass
        except Exception as e:
            logger.error(
                f"Ошибка при удалении сообщения по тайм-ауту: {e}", exc_info=True
            )


class ActionSelect(discord.ui.Select):
    """### Выпадающий список для выбора действия в панели управления
    Содержит варианты "Выключить бота" и "Перезагрузить модуль", после выбора выполняет соответствующее действие"""

    def __init__(self, bot: "Bot", interaction: discord.Interaction):
        self.bot = bot
        self.initial_interaction = interaction
        options = [
            discord.SelectOption(
                label="Выключить бота", description="Прекращает работу бота", value="shutdown", emoji="🛑"),
            discord.SelectOption(label="Перезагрузить модуль",
                                 description="Перезагружает выбранный ког", value="reload", emoji="⚙️")
        ]
        super().__init__(placeholder="Выберите действие...",
                         min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Получаем выбранное действие
        action = self.values[0]
        logger.info(
            f'Было выбрано действие "{action}" в панели управления ботом')
        # Если нужно выключить бота
        if action == "shutdown":
            # Отправляем сообщение о выключении бота
            embed = create_embed(
                title="Бот успешно выключен",
                description="Соединение с базой данных закрыто, клиент Discord отключён",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            # Логгируем выключение бота
            logger.info("Бот выключен по команде разработчика")
            # Закрываем соединения с БД и соединение клиента с Discord
            if self.bot.db_pool:
                await self.bot.db_pool.close()
            await self.bot.close()
        # Если нужно перезагрузить модуль
        elif action == "reload":
            logger.info("Начата процедура перезагрузки модуля")
            # Отправляем сообщение с формой выбора модуля для перезагрузки
            embed = create_embed(
                title="Перезагрузка модулей",
                description="Выберите нужный модуль из списка ниже:",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, view=ModuleReloadView(self.bot, self.initial_interaction), ephemeral=True)


class ManageView(discord.ui.View):
    """### UI-представление для панели управления ботом
    Содержит в себе выпадающий список :class:`ActionSelect` для выбора действия"""

    def __init__(self, bot: "Bot", interaction: discord.Interaction):
        super().__init__(timeout=120)
        self.add_item(ActionSelect(bot, interaction))
        self.initial_interaction = interaction

    async def on_timeout(self):
        # По истечении тайм-аута пробуем удалить исходное сообщение
        try:
            logger.info("Время работы панели управления истекло")
            await self.initial_interaction.delete_original_response()
        except discord.NotFound:
            pass
        except Exception as e:
            logger.error(
                f"Ошибка при удалении сообщения по тайм-ауту: {e}", exc_info=True
            )


class Admin(commands.Cog):
    """### Модуль с административными командами для управления/настройки бота

    - Команда :meth:`manage` для управления ботом"""

    def __init__(self, bot: "Bot"):
        self.bot = bot

    @app_commands.command(name="manage", description="Панель управления ботом (только для разработчика)")
    async def manage(self, interaction: discord.Interaction):
        # Проверяем, является ли пользователь разработчиком
        if interaction.user.id != CREATOR_ID:
            logger.warning(
                f'Пользователь {interaction.user.display_name} попытался получить доступ к панели управления ботом')
            embed = create_embed(
                title="У Вас нет доступа к этой команде",
                description="Данная команда доступна только разработчику, оставьте ему эту работу ◑﹏◐",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        # Создаём интерфейс панели управления и отправляем его пользователю
        logger.info('Запущена панель управления ботом')
        embed = create_embed(
            title="Панель управления Slashy",
            description="Выберите нужное действие из выпадающего списка ниже",
            color=discord.Color.blue()
        )
        view = ManageView(self.bot, interaction)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
