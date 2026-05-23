import time
import discord
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

        Returns:
            list[discord.Member]: Список валидных пользователей (всех, кроме ботов)
        """
        logger.debug(
            f'Начато выполнение get_valid_voice_members - получение валидных пользователей (не являющихся ботами) \
для голосового канала {channel.id} ({channel.name}) на сервере {channel.guild.id} ({channel.guild.name})')
        # Возвращаем список участников канала, исключая ботов
        if not filtered:
            return [m for m in channel.members if not m.bot]
        # Проверяем, что пул соединений с базой данных инициализирован
        if not self.bot.db_pool:
            logger.warning(
                'Пул соединений с базой данных не инициализирован. Пропуск фильтрации участников голосового канала')
            return
        async with self.bot.db_pool.acquire() as conn:
            settings = await conn.fetch(
                """
                SELECT user_id, vc_stats_enabled
                FROM user_settings
                WHERE guild_id = $1
            """,
                channel.guild.id
            )
            d = {r[0]: r[1] for r in settings}
            return [m for m in channel.members if not m.bot and d.get(m.id)]

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

    async def stop_tracking(self, member: discord.Member):
        """### Функция прекращения отслеживания времени "общения" пользователя
        Сохраняет данные пользователя, когда он выходит из голосового канала и очищает данные сессии

        Args:
            member (discord.Member): Пользователь, для которого нужно сохранить данные
        """
        logger.debug(
            f'Начато выполнение stop_tracking - прекращение сессии "общения" для пользователя {member.id} ({member.display_name}) \
на сервере {member.guild.id} ({member.guild.name})')
        # Получаем данные о сессии и удаляем их из списка активных сессий
        logger.debug(
            f'Получение данных сессии "общения" для пользователя {member.id} ({member.display_name}) из списка активных сессий')
        session = self.active_sessions.pop(member.id, None)
        if session:
            logger.debug(
                f'Данные сессии "общения" для пользователя {member.id} ({member.display_name}) успешно получены')
            # Получаем время "общения" пользователя, как разницу между текущим временем и временем начала сессии
            # И сохраняем данные
            duration = int(time.time() - session['start'])
            await self.save_time(member.id, session['guild_id'], duration)
        else:
            logger.warning(
                f'Данные сессии "общения" для пользователя {member.id} ({member.display_name}) не найдены')

    async def start_tracking(self, member: discord.Member):
        """### Функция начала отслеживания времени "общения" пользователя
        Сохраняет время начала сессии "общения" пользователя
        Args:
            member (discord.Member): Пользователь, для которого нужно создать сессию "общения"
        """
        # Создаём сессию "общения" для пользователя, если её ещё нет
        # Указываем текущее время и айди сервера
        logger.debug(
            f'Начато выполнение start_tracking - создание сессии "общения" для пользователя {member.id} ({member.display_name}) \
на сервере {member.guild.id} ({member.guild.name})')
        if member.id not in self.active_sessions:
            self.active_sessions[member.id] = {
                'start': time.time(),
                'guild_id': member.guild.id
            }
            logger.debug(
                f'Сессия "общения" для пользователя {member.id} ({member.display_name}) на сервере {member.guild.id} ({member.guild.name}) создана')

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """### Обработчик изменения голосового канала
        Используется для обработки подключений и отключений пользователей к голосовым каналам

        Args:
            member (discord.Member): Пользователь, для которого изменилось состояние
            before (discord.VoiceState): Состояние голосового канала до изменения
            after (discord.VoiceState): Состояние голосового канала после изменения
        """
        logger.debug(
            f'Обработка изменения голосового канала для пользователя {member.id} ({member.display_name}) \
на сервере {member.guild.id} ({member.guild.name})')
        # Пропускаем обработку для ботов
        if member.bot:
            logger.debug(
                f'Пользователь {member.id} ({member.display_name}) является ботом - пропуск обработки')
            return
        # Игнорируем события внутри одного канала (нас интересует только смена канала)
        if before.channel == after.channel:
            logger.debug(
                f'Пользователь {member.id} ({member.display_name}) не изменил канал - пропуск обработки')
            return
        # Проверяем, что пул соединений с базой данных инициализирован
        if not self.bot.db_pool:
            logger.warning(
                'Пул соединений с базой данных не инициализирован. Пропуск обработки подключения')
            return
        # Проверяем, что есть разрешения на сбор статистики времени "общения"
        logger.debug(f'Проверяем разрешения на сбор статистики времени "общения" для пользователя \
{member.id} ({member.display_name}) на сервере {member.guild.id} ({member.guild.name})')
        async with self.bot.db_pool.acquire() as conn:
            # Проверяем, что на сервере включён сбор статистики времени "общения"
            row = await conn.fetchrow(
                """
                SELECT vc_stats_enabled FROM guild_settings
                WHERE guild_id = $1
            """,
                member.guild.id,
            )
            # Если на сервере запрещён сбор статистики
            if row and row[0] is False:
                logger.debug(f'На сервере {member.guild.id} ({member.guild.name}) запрещён сбор статистики времени "общения" \
- пропуск обработки')
                return
            # Если нет разрешения на сбор статистики (не указано, можно ли её собирать)
            if not row:
                logger.debug(f'На сервере {member.guild.id} ({member.guild.name}) не разрешён (не указан) сбор статистики времени "общения" \
- пропуск обработки')
                return
            # Проверяем пользовательские настройки по сбору статистики
            row = await conn.fetchrow(
                """
                SELECT vc_stats_enabled FROM user_settings
                WHERE guild_id = $1 AND user_id = $2
            """,
                member.guild.id,
                member.id,
            )
            # Если пользователь запретил сбор статистики
            if row and row[0] is False:
                logger.debug(f'Пользователь {member.id} ({member.display_name}) запретил сбор статистики времени "общения" \
- пропуск обработки')
                return
            # Если пользователь не указал настройку
            # Устанавливаем на "Разрешено", т.к. на сервере включён сбор статистики
            # И пользователи должны вручную устанавливать запрет
            if not row:
                logger.debug(f'У пользователя {member.id} ({member.guild.id}) не указан запрет на сбор статистики времени "общения" \
а на сервере {member.guild.id} ({member.guild.name}) разрешён сбор статистики - создаём запись с настройкой, разрещающей сбор')
                await conn.execute(
                    """
                    INSERT INTO user_settings (guild_id, user_id, vc_stats_enabled)
                    VALUES ($1, $2, $3)
                """,
                    member.guild.id,
                    member.id,
                    True,
                )
            logger.debug(f'Разрешение на сбор статистики времени "общения" пользователя \
{member.id} ({member.display_name}) на сервере {member.guild.id} ({member.guild.name}) есть - переходим к дальнейшей обработке')
        # Если пользователь покинул канал
        if before.channel:
            # Прекращаем сессию "общения" для пользователя
            logger.debug(
                f'Пользователь {member.id} ({member.display_name}) покинул канал {before.channel.id}')
            await self.stop_tracking(member)
            # Получаем список оставшихся пользователей (без ботов)
            valid_members = await self.get_valid_voice_members(before.channel)
            if not valid_members:
                return
            # Если осталось меньше 2 человек, прекращаем сессию "общения" для оставшегося пользователя
            if len(valid_members) < 2:
                logger.debug(
                    f'В канале {before.channel.id} осталось меньше 2 человек')
                for m in valid_members:
                    await self.stop_tracking(m)
        # Если пользователь подключился к каналу
        if after.channel:
            logger.debug(
                f'Пользователь {member.id} ({member.display_name}) подключился к каналу {after.channel.id}')
            # Получаем список пользователей в канале (без ботов), учитывая их разрешение на сбор статистики времени общения
            valid_members = await self.get_valid_voice_members(after.channel, True)
            if not valid_members:
                return
            # Если в канале как минимум 2 человека, запускаем сессию "общения" для каждого
            if len(valid_members) >= 2:
                logger.debug(
                    f'В канале {after.channel.id} как минимум 2 человека')
                for m in valid_members:
                    await self.start_tracking(m)

    @tasks.loop(minutes=30.0)
    async def save_sessions_task(self):
        """### Задача автосохранения сессий "общения"
        Сохраняет активные сессии "общения" пользователей каждые 30 минут на случай внезапного отключения бота
        """
        # Получаем текущее время и сохраняем данные для всех активных сессий, обновляя время начала
        logger.debug(
            'Начато выполнение save_sessions_task - автосохранение активных сессий "общения"')
        now = time.time()
        for user_id, data in list(self.active_sessions.items()):
            duration = int(now - data['start'])
            if duration > 0:
                await self.save_time(user_id, data["guild_id"], duration)
                self.active_sessions[user_id]["start"] = now
        logger.debug(
            'Завершено выполнение save_sessions_task - активные сессии "общения" сохранены')

    @save_sessions_task.before_loop
    async def before_save_sessions(self):
        """### Функция перед запуском задачи автосохранения сессий
        Ждёт готовности бота до запуска автосохранения, чтобы избежать ошибок при попытке доступа к базе данных до её инициализации
        """
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Events(bot))
