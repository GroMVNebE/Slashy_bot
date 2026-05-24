import time
import discord
import asyncio
from discord.ext import commands, tasks
import logging
from utils import add_xp
from typing import TYPE_CHECKING

# Подлючаем типизацию для класса Bot из launch.py, избегая циклического импорта
# Нужно для правильного определения типов в IDE
if TYPE_CHECKING:
    from launch import Bot

# Инициализируем логгер для этого модуля
logger = logging.getLogger('slashy.events')


class Events(commands.Cog):
    def __init__(self, bot: 'Bot'):
        self.bot = bot
        # Словарь активных сессий "общения"
        # Хранит данные о времени пользователей в голосовых каналах, при условии что в канале 2+ человека
        # Формат: {user_id: {"start": timestamp_начала, "guild_id": ID_сервера}}
        self.active_sessions = {}
        # Сессии на паузе (вылетевшие): {user_id: {"start": ts, "guild_id": id, "expires_at": ts_окончания_ожидания}}
        self.pending_sessions = {}
        # Запускаем фоновую задачу сохранения
        self.save_sessions_task.start()

    async def cog_unload(self):
        """### Обработчик выгрузки кога
        При перезагрузке кога останавливает задачу сохранения сессий "общения", чтобы избежать ошибок при повторном запуске кога
        (избавиться от двух работающих задач одновременно)
        """
        # Отключаем фоновую задачу сохранения сессий
        logger.info(
            'Начато выполнение cog_unload - производится отключение фоновой задачи сохранения сессий "общения"')
        self.save_sessions_task.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """### Обработчик отправки сообщения
        - Выдаёт пользователю опыт за отправку сообщений
        Args:
            message (discord.Message): Отправленное пользователем сообщение со всеми данными
        """
        logger.debug(
            f'Обработка отправленного сообщения от пользователя {message.author.id} ({message.author.display_name}) \
на сервере {message.author.guild.id if type(message.author) == discord.Member else "Нет данных"} \
({message.author.guild.name if type(message.author) == discord.Member else "Нет данных"})')
        # Пропускаем сообщения от ботов
        if message.author.bot:
            logger.debug(
                f'Автор сообщения является ботом - пропуск обработки сообщения')
            return
        # Пропускаем сообщения, отправленные в ЛС
        if not message.guild:
            logger.debug(
                f'Сообщение отправлено в ЛС - пропуск обработки сообщения')
            return
        # Проверяем, что пул соединений с базой данных инициализирован
        if not self.bot.db_pool:
            logger.warning(
                'Пул соединений с базой данных не инициализирован. Пропуск обработки сообщения')
            return
        # Начисляем опыт за сообщение
        await add_xp(user_id=message.author.id, guild_id=message.guild.id, xp=10, pool=self.bot.db_pool)

    async def get_valid_voice_members(self, channel: discord.VoiceChannel | discord.StageChannel, filtered: bool = False):
        """### Функция для получения валидных пользователей в голосовом канале
        Валидные пользователи - все, кроме ботов

        Args:
            channel (discord.VoiceChannel | discord.StageChannel): Голосовой канал, для которого нужно получить список валидных пользователей
            filtered (bool): Если True, оставляет только пользователей с разрешенным сбором статистики

        Returns:
            list[discord.Member]: Список валидных пользователей
        """
        logger.debug(
            f'Начато выполнение get_valid_voice_members - получение валидных пользователей (не являющихся ботами) '
            f'для голосового канала {channel.id} ({channel.name}) на сервере {channel.guild.id} ({channel.guild.name})')

        if not filtered:
            return [m for m in channel.members if not m.bot]

        if not self.bot.db_pool:
            logger.warning(
                'Пул соединений с базой данных не инициализирован. Пропуск фильтрации участников голосового канала')
            return []

        async with self.bot.db_pool.acquire() as conn:
            guild_row = await conn.fetchrow(
                "SELECT vc_stats_enabled FROM guild_settings WHERE guild_id = $1",
                channel.guild.id
            )
            if not guild_row or guild_row['vc_stats_enabled'] is False:
                logger.debug(
                    f'На сервере {channel.guild.id} отключен или не настроен сбор статистики')
                return []

            settings = await conn.fetch(
                "SELECT user_id, vc_stats_enabled FROM user_settings WHERE guild_id = $1",
                channel.guild.id
            )
            user_settings_dict = {r['user_id']: r['vc_stats_enabled'] for r in settings}
            valid_members = []
            for m in channel.members:
                if m.bot:
                    continue
                user_enabled = user_settings_dict.get(m.id, True)
                if user_enabled is True:
                    valid_members.append(m)
                    if m.id not in user_settings_dict:
                        logger.debug(
                            f'У пользователя {m.id} не указана настройка, создаем запись со значением True')
                        try:
                            await conn.execute(
                                """
                                INSERT INTO user_settings (guild_id, user_id, vc_stats_enabled)
                                VALUES ($1, $2, $3)
                                ON CONFLICT (guild_id, user_id) DO NOTHING
                                """,
                                channel.guild.id, m.id, True
                            )
                        except Exception as e:
                            logger.warning(
                                f'Ошибка автоматического создания настройки для {m.id}: {e}')

            return valid_members

    async def save_time(self, user_id: int, guild_id: int, duration: int):
        """### Функция для сохранения сессии "общения" в базе данных
        Сохраняет время общения пользователя при условии, что сессия длилась хотя бы 3 секунды

        Args:
            user_id (int): ID пользователя
            guild_id (int): ID сервера
            duration (int): Продолжительность сессии в секундах
        """
        logger.debug(
            f'Начато выполнение save_time - сохранение сессии "общения" для пользователя {user_id} \
на сервере {guild_id} с продолжительностью {duration} сек.')
        # Проверяем, что сессия длилась хотя бы 3 секунды
        if duration < 3:
            logger.warning(
                f'Сессия пользователя {user_id} меньше 3 секунд, пропуск сохранения')
            return
        # Сохраняем время в базе данных, используя UPSERT для обновления существующей записи или создания новой
        try:
            # Проверяем, что пул соединений с базой данных инициализирован
            if not self.bot.db_pool:
                logger.warning(
                    f'Пул соединений с базой данных не инициализирован. Пропуск сохранения статистики "общения" для пользователя {user_id}')
                return
            # Сохраняем данные в БД
            logger.debug(
                f'Сохранение данных сессии "общения" пользователя {user_id} в БД')
            async with self.bot.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO voice_stats (user_id, guild_id, day, seconds)
                    VALUES ($1, $2, CURRENT_DATE, $3)
                    ON CONFLICT (user_id, guild_id, day)
                    DO UPDATE SET seconds = voice_stats.seconds + EXCLUDED.seconds;
                    """,
                    user_id, guild_id, duration
                )
                logger.debug(
                    f'Сохранение данных сессии "общения" пользователя {user_id} в БД: Успешно')
        except Exception as e:
            logger.warning(
                f'Ошибка при сохранении данных сессии "общения" пользователя {user_id} в БД: {e}', exc_info=True)

    async def start_tracking(self, member: discord.Member):
        if member.id in self.active_sessions:
            return

        if member.id in self.pending_sessions:
            logger.debug(
                f'Пользователь {member.id} ({member.display_name}) вернулся из pending_sessions. Восстанавливаем сессию.')
            session = self.pending_sessions.pop(member.id)
            session.pop('expires_at', None)
            self.active_sessions[member.id] = session
            return

        self.active_sessions[member.id] = {
            'start': time.time(),
            'guild_id': member.guild.id
        }
        logger.debug(
            f'Создана новая сессия для пользователя {member.id} ({member.display_name})')

    async def stop_tracking(self, member: discord.Member, grace_period: int = 180):
        session = self.active_sessions.pop(member.id, None)
        if session:
            session['expires_at'] = time.time() + grace_period
            self.pending_sessions[member.id] = session
            logger.debug(
                f'Сессия пользователя {member.id} ({member.display_name}) отправлена в pending_sessions на {grace_period} сек.')

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """### Обработчик изменения голосового канала
        Используется для обработки подключений и отключений пользователей к голосовым каналам"""
        if member.bot:
            return
        if before.channel == after.channel:
            return
        if not self.bot.db_pool:
            logger.warning(
                'Пул соединений с базой данных не инициализирован. Пропуск обработки.')
            return
        logger.debug(
            f'Обработка изменения голосового канала для пользователя {member.id} ({member.display_name}) '
            f'на сервере {member.guild.id} ({member.guild.name})')

        if before.channel:
            logger.debug(
                f'Пользователь {member.id} ({member.display_name}) покинул канал {before.channel.id}')
            if after.channel is None:
                await self.stop_tracking(member)
            remaining_members = await self.get_valid_voice_members(before.channel, filtered=False)
            if len(remaining_members) < 2:
                logger.debug(
                    f'В канале {before.channel.id} ({before.channel.name}) осталось меньше 2 человек. Останавливаем трекинг для всех.')
                for m in remaining_members:
                    await self.stop_tracking(m)

        if after.channel:
            logger.debug(
                f'Пользователь {member.id} ({member.display_name}) подключился к каналу {after.channel.id}')
            all_channel_members = await self.get_valid_voice_members(after.channel, filtered=False)
            if len(all_channel_members) >= 2:
                logger.debug(
                    f'В канале {after.channel.id} ({after.channel.name}) как минимум 2 человека. Запускаем трекинг для разрешенных.')
                allowed_members = await self.get_valid_voice_members(after.channel, filtered=True)
                for m in allowed_members:
                    await self.start_tracking(m)
            else:
                logger.debug(
                    f'В канале {after.channel.id} меньше 2 человек. Никого не отслеживаем.')

    @tasks.loop(seconds=30.0)
    async def save_sessions_task(self):
        now = time.time()

        for user_id, data in list(self.pending_sessions.items()):
            if now >= data['expires_at']:
                grace_time = int(data['expires_at'] - now)
                duration = int(data['expires_at'] - data['start']) - grace_time

                self.pending_sessions.pop(user_id, None)
                if duration > 0:
                    await self.save_time(user_id, data["guild_id"], duration)
                    logger.debug(
                        f'Льготный период истек. Сессия {user_id} окончательно сохранена в БД.')

        if int(now) % 1800 < 30:
            logger.debug('Плановое автосохранение активных сессий...')
            for user_id, data in list(self.active_sessions.items()):
                duration = int(now - data['start'])
                if duration > 0:
                    await self.save_time(user_id, data["guild_id"], duration)
                    self.active_sessions[user_id]["start"] = now

    @save_sessions_task.before_loop
    async def before_save_sessions(self):
        """### Функция перед запуском задачи автосохранения сессий
        Ждёт готовности бота до запуска автосохранения, чтобы избежать ошибок при попытке доступа к базе данных до её инициализации
        """
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Events(bot))
