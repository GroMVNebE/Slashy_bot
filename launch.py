import os
import logging
from logging.handlers import TimedRotatingFileHandler
import asyncio
import asyncpg
import discord
from discord.ext import commands
from utils import *

# Создаем папку для логов, если её нет
if not os.path.exists('logs'):
    os.makedirs('logs')

# Настраиваем формат: [Дата Время] [Уровень] [Имя_модуля]: Сообщение
log_formatter = logging.Formatter(
    '%(asctime)s | %(levelname)s | %(name)s: %(message)s')

# Настраиваем обработчик для файлов с ротацией по дате
# Ротация будет происходить каждый день в полночь, сохранять 30 последних файлов логов и добавлять дату к имени файла
file_handler = TimedRotatingFileHandler(
    filename=f'logs/slashy.log',
    when='midnight',
    interval=1,
    backupCount=30,
    encoding='utf-8',
)
file_handler.setFormatter(log_formatter)
file_handler.suffix = '%Y-%m-%d'

# Обработчик для вывода в консоль
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

# Инициализируем логгер
logging.basicConfig(level=logging.DEBUG, handlers=[
                    file_handler, console_handler])

logger = logging.getLogger('slashy')

# Получаем переменные окружения
TOKEN = get_env('DISCORD_TOKEN')
DATABASE_URL = get_env('DATABASE_URL')


class Bot(commands.Bot):

    def __init__(self):
        # Настраиваем разрешения
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(command_prefix='!', intents=intents, help_command=None)
        self.db_pool: asyncpg.Pool | None = None

    async def setup_hook(self):
        logger.debug(
            'Начато выполнение setup_hook - производится первоначальная настройка бота')
        # Устанавливаем обработчик ошибок для всех слэш-команд
        self.tree.on_error = self.on_tree_error
        # Создаём пул соединений с базой данных при запуске бота
        logger.debug('Создание пула соединений с БД')
        try:
            self.db_pool = await asyncpg.create_pool(dsn=DATABASE_URL)
            logger.info('Создание пула соединений с БД: Успешно')
        except Exception as e:
            logger.error(
                f'Ошибка при создании пула соединений с БД: {e}', exc_info=True)
        # Проверяем, что пул соединений был успешно создан
        if not self.db_pool:
            logger.critical(
                'Критическая ошибка: Не удалось создать пул соединений с базой данных')
            await self.close()
            return
        # Запускаем настройку базы данных (создание таблиц, если их нет)
        await setup_database(self.db_pool)

        # Загружаем модули расширения
        logger.debug('Загрузка модулей расширения из ./cogs')
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    logger.info(f'Модуль {filename} успешно загружен')
                except Exception as e:
                    logger.error(
                        f'Ошибка при загрузке модуля {filename}: {e}', exc_info=True
                    )
        # Синхронизируем команды с Discord API
        logger.debug('Синхронизация команд с Discord API')
        try:
            synced = await self.tree.sync()
            logger.info(
                f'Завершена синхронизация команд, было синхронизировано {len(synced)} команд')
        except Exception as e:
            logger.error(
                f'Ошибка при синхронизации команд: {e}', exc_info=True)

    async def close(self):
        # Закрываем пул соединений при завершении работы бота
        if self.db_pool:
            await self.db_pool.close()
        await super().close()

    async def on_tree_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        # Если ошибка - ограничение по частоте использования команды
        if isinstance(error, discord.app_commands.CommandOnCooldown):
            logger.debug(
                f'Пользователь {user_data(interaction)} попытался использовать команду {interaction.command} \
до истечения времени ожидания ({error.retry_after:.1f} сек.) на сервере {server_data(interaction)}')
            # Отправляем пользователю сообщение с информацией о том,
            # Сколько времени осталось до возможности повторного использования команды
            embed = create_embed(
                title='Подождите перед повторным использованием команды',
                description=f'Подождите ещё **{error.retry_after:.1f} сек.** перед повторным использованием команды',
                color=discord.Color.red(),
            )
            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )


async def main():
    slashy = Bot()
    async with slashy:
        await slashy.start(TOKEN)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
