import time
import discord
from discord.ext import commands, tasks
import logging
from utils import *
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import TYPE_CHECKING

# Подлючаем типизацию для класса Bot из launch.py, избегая циклического импорта
# Нужно для правильного определения типов в IDE
if TYPE_CHECKING:
    from launch import Bot

# Инициализируем логгер для этого модуля
logger = logging.getLogger('slashy.events')


class Events(commands.Cog):
    """### Модуль обработки Событий в Discord
    Обрабатывает:
    - Отправку сообщения
    - Изменение состояния голосового канала (подключение/отключение)
    """

    def __init__(self, bot: 'Bot') -> None:
        """### Модуль обработки Событий в Discord
        Обрабатывает:
        - Отправку сообщения
        - Изменение состояния голосового канала (подключение/отключение)
        Args:
            bot (:class:`Bot`): Запущенный Дискорд-бот
        """
        self.bot = bot
        """### Запущенный :class:`Bot`"""
        self.active_sessions = {}
        """### Словарь активных сессий "общения"
        Хранит данные о времени пользователей в голосовых каналах, при условии что в канале 2+ человека  
        Формат: {user_id: {"prd_start": Время начала текущего периода, "session_start": Время начала сессии, "guild_id": ID сервера}}
        """
        self.pending_sessions = {}
        """### Словарь истекающих сессий "общения"
        Истекающие сессии :data:`active_sessions`  
        Формат: {user_id: {"prd_start": Время начала периода, "session_start": Время начала сессии, "guild_id": ID сервера,  
        "expires_at": Время истечения сессии, "left_at": Время отключения пользователя от ГК}}
        """
        # Запускаем фоновую задачу сохранения
        self.save_sessions_task.start()
        # Запускаем фоновую задачу удаления истёкших сессий
        self.delete_pending_sessions_task.start()

    async def cog_unload(self) -> None:
        """### Обработчик выгрузки кога
        При перезагрузке кога останавливает задачу сохранения сессий "общения", чтобы избежать ошибок при повторном запуске кога
        (избавиться от двух работающих задач одновременно), а также сохраняет информацию о сессиях "общения"
        """
        # Отключаем фоновые задачи
        logger.info(
            'Начато выполнение cog_unload - сохранение сессий "общения"')
        self.save_sessions_task.cancel()
        self.delete_pending_sessions_task.cancel()
        # Сохраняем все сессии "общения" пользователей
        try:
            for user_id, data in list(self.active_sessions.items()):
                await self.save_time(user_id, data, False)
                self.active_sessions.pop(user_id, None)
            for user_id, data in list(self.pending_sessions.items()):
                await self.save_time(user_id, data, False)
                self.pending_sessions.pop(user_id, None)
        except Exception as e:
            logger.error(
                f'В процессе сохранения сессий "общения" произошла ошибка: {e}', exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """### Обработчик отправки сообщения
        Выдаёт пользователю опыт за отправку сообщений
        Args:
            message (:class:`discord.Message`): Отправленное пользователем сообщение
        """
        # Пропускаем сообщение, если оно отправлено в ЛС
        if type(message.author) != discord.Member:
            logger.warning(
                'Сообщение отправлено не на сервере - пропуск обработки')
            return
        logger.debug(
            f'Обработка отправленного сообщения от пользователя {get_info(message.author)} на сервере {get_info(message.author.guild)}')
        # Пропускаем сообщения от ботов
        if message.author.bot:
            logger.debug(
                f'Автор сообщения является ботом - пропуск обработки сообщения')
            return
        # Проверяем, что пул соединений с базой данных инициализирован
        if not self.bot.db_pool:
            logger.warning(
                'Пул соединений с базой данных не инициализирован. Пропуск обработки сообщения')
            return
        # Начисляем опыт за сообщение
        await add_xp(user_id=message.author.id, guild_id=message.author.guild.id, xp=10, pool=self.bot.db_pool)

    async def get_valid_voice_members(self, channel: discord.VoiceChannel | discord.StageChannel, filtered: bool = False) -> list[discord.Member]:
        """### Функция для получения валидных пользователей в голосовом канале
        Валидные пользователи - все, кроме ботов

        Args:
            channel (:class:`discord.VoiceChannel` | :class:`discord.StageChannel`): Голосовой канал, для которого нужно получить список валидных пользователей
            filtered (bool): Если True, оставляет только пользователей с разрешённым сбором статистики

        Returns:
            list[discord.Member]: Список валидных пользователей
        """
        logger.debug(
            f'Начато выполнение get_valid_voice_members - получение валидных пользователей (не являющихся ботами) '
            f'для голосового канала {channel.id} ({channel.name}) на сервере {get_info(channel.guild)}')
        # Если нужны все валидные пользователи - отсекаем ботов
        if not filtered:
            logger.debug(
                'Получение всех валидных пользователей без фильтрации...')
            return [m for m in channel.members if not m.bot]
        # Если нужно проверить разрешения на сбор статистики
        async with self.bot.db_pool.acquire() as conn:
            logger.debug(
                'Получение всех валидных пользователей с учётом разрешения на сбор статистики')
            # Получаем значение настройки сбора статистики на сервере
            guild_row = await conn.fetchrow(
                "SELECT vc_stats_enabled FROM guild_settings WHERE guild_id = $1",
                channel.guild.id
            )
            # Если сбор статистики не указан или запрещён - возвращаем пустой список
            if not guild_row or guild_row['vc_stats_enabled'] is False:
                logger.debug(
                    f'На сервере {channel.guild.id} отключен или не настроен сбор статистики')
                return []
            # Получаем значения настроек пользователей
            settings = await conn.fetch(
                "SELECT user_id, vc_stats_enabled FROM user_settings WHERE guild_id = $1",
                channel.guild.id
            )
            user_settings_dict = {r['user_id']
                : r['vc_stats_enabled'] for r in settings}
            # Собираем список пользователей, у которых разрешён сбор статистики
            valid_members = []
            for m in channel.members:
                # Пропускаем ботов
                if m.bot:
                    logger.debug(
                        f'Пользователь {get_info(m)} - бот, пропускаем')
                    continue
                # Добавляем пользователя, если у него не указан запрет на сбор статистики
                if user_settings_dict.get(m.id, True) is True:
                    logger.debug(
                        f'Пользователь {get_info(m)} не указал запрет на сбор статистики времени "общения" - '
                        'добавляем в список валидных пользователей')
                    valid_members.append(m)
            ln = len(valid_members)
            logger.debug(
                f'Собран список из {ln} {get_plural(ln, ("валидного пользователя", "валидных пользователей", "валидных пользователей"))}')
            return valid_members

    async def save_time(self, user_id: int, session: dict, autosave: bool):
        """### Функция для сохранения сессии "общения" в базе данных
        Сохраняет время общения пользователя при условии, что сессия длилась хотя бы 3 секунды

        Args:
            user_id (int): ID пользователя
            session (dict): Сессия "общения" из :data:`active_sessions` | :data:`pending_sessions`
            duration (int): Продолжительность сессии в секундах
        """
        logger.debug(f'Получение данных сессии пользователя {user_id}...')
        try:
            end_time: float = session.get('left_at', time.time())
            prd_duration = int(end_time - session['prd_start'])
            ssn_start: datetime = session['session_start']
            ssn_end = datetime.fromtimestamp(
                end_time, ZoneInfo("Europe/Moscow"))
            ssn_duration = int((ssn_end - ssn_start).total_seconds())
            guild_id: int = session['guild_id']
            logger.debug(
                f'Получены данные сессии {user_id}: {session["prd_start"]} - {end_time} ({prd_duration}), {ssn_start} - {ssn_end} ({ssn_duration}), ID: {guild_id}')
        except Exception as e:
            logger.error(
                f'Ошибка при получении данных сессии "общения" пользователя {user_id}: {e}', exc_info=True)
            return
        logger.debug(
            f'Начато выполнение save_time - сохранение {"периода" if autosave else "сессии"} "общения" для пользователя {user_id} '
            f'на сервере {guild_id} с продолжительностью {prd_duration} ({ssn_duration}) сек.')
        if prd_duration < 0:
            logger.error(
                f'Некорректное время общения: {prd_duration} < 0')
            return
        # Проверяем, что сессия длилась хотя бы 15 секунд
        if ssn_duration < 15:
            logger.warning(
                f'Сессия пользователя {user_id} меньше 15 секунд, пропуск сохранения')
            return
        # Сохраняем время в базе данных, используя UPSERT для обновления существующей записи или создания новой
        try:
            # Сохраняем данные в БД
            logger.debug(
                f'Сохранение данных сессии "общения" пользователя {user_id} в БД')
            async with self.bot.db_pool.acquire() as conn:
                # Если происходит автосохранение - обновляем время общения пользователя за текущий день
                if autosave:
                    await conn.execute(
                        """
                        INSERT INTO voice_stats (user_id, guild_id, day, seconds)
                        VALUES ($1, $2, CURRENT_DATE, $3)
                        ON CONFLICT (user_id, guild_id, day)
                        DO UPDATE SET seconds = voice_stats.seconds + EXCLUDED.seconds;
                        """,
                        user_id, guild_id, prd_duration
                    )
                # Если данные сохраняются после того, как пользователь покинул ГК
                # Сохраняем все данные сессии - время общения, максимальную сессию и продолжительность последней сессии
                else:
                    stats_query = """
                    INSERT INTO voice_stats (user_id, guild_id, day, seconds)
                    VALUES ($1, $2, CURRENT_DATE, $3)
                    ON CONFLICT (user_id, guild_id, day)
                    DO UPDATE SET seconds = voice_stats.seconds + EXCLUDED.seconds;
                    """
                    await conn.execute(
                        f'{stats_query}',
                        user_id, guild_id, prd_duration
                    )
                    max_sessions_query = """
                    INSERT INTO voice_max_sessions (user_id, guild_id, day, max_seconds)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (guild_id, user_id, day)
                    DO UPDATE SET max_seconds = GREATEST(voice_max_sessions.max_seconds, EXCLUDED.max_seconds);
                    """
                    await conn.execute(
                        f'{max_sessions_query}',
                        user_id, guild_id, ssn_start, ssn_duration
                    )
                    if ssn_duration > 60*5:
                        await conn.execute(
                            """
                            INSERT INTO voice_detailed_sessions(user_id, guild_id, start_time, end_time, seconds)
                            VALUES ($1, $2, $3, $4, $5);
                        """,
                            user_id, guild_id, ssn_start, ssn_end, ssn_duration
                        )
                logger.debug(
                    f'Сохранение данных {"периода" if autosave else "сессии"} "общения" пользователя {user_id} в БД: Успешно')
        except Exception as e:
            logger.warning(
                f'Ошибка при сохранении данных сессии "общения" пользователя {user_id} в БД: {e}', exc_info=True)

    async def start_tracking(self, member: discord.Member):
        """### Функция для начала отслеживания времени общения
        Создаёт сессию общения пользователя, сохраняя время его подключения к голосовому каналу для дальнейшей обработки

        Args:
            member (:class:`discord.Member`): Участник сервера, для которого нужно создать сессию общения
        """
        logger.debug(f'Выполнение start_tracking для {get_info(member)}:')
        # Если у пользователя уже есть активная сессия (пр. к каналу подключился ещё один участник)
        # Новую сессию создавать не требуется - пропускаем
        if member.id in self.active_sessions:
            logger.debug(
                f'У {get_info(member)} уже есть активная сессия общения - пропуск start_tracking')
            return
        # Если пользователь есть в pending_sessions - он вернулся в голосовой канал
        # Переносим в active_sessions
        if member.id in self.pending_sessions:
            logger.debug(
                f'Пользователь {member.id} ({member.display_name}) вернулся из pending_sessions. Восстанавливаем сессию.')
            session = self.pending_sessions.pop(member.id)
            session.pop('expires_at', None)
            session.pop('left_at', None)
            self.active_sessions[member.id] = session
            return
        # В остальных случаях создаём активную сесисю для пользователя
        # Сохраняя нужные данные
        self.active_sessions[member.id] = {
            'prd_start': time.time(),
            'session_start': datetime.now(ZoneInfo("Europe/Moscow")),
            'guild_id': member.guild.id
        }
        logger.debug(
            f'Создана новая сессия для пользователя {get_info(member)}')

    async def stop_tracking(self, member: discord.Member, grace_period: int = 180):
        """### Функция для завершения отслеживания сессии общения
        Переносит сессию общения в список истекающих, что в дальнейшем приведёт к её сохранению в БД и очистке

        Args:
            member (:class:`discord.Member`): Участник сервера, сессию общения которого нужно прекратить отслеживать
            grace_period (int, optional): Период до переноса данных о сессии общения в БД, по умолчанию составляет 3 минуты (180 сек.)
        """
        logger.debug(f'Выполнение stop_tracking для {get_info(member)}')
        # Удаляем сессию общения пользователя из списка активных сессий
        session: dict = self.active_sessions.pop(member.id, None)
        # Если у пользователя была сессия, переносим её в список истекающих
        if session:
            session['expires_at'] = time.time() + grace_period
            session['left_at'] = time.time()
            self.pending_sessions[member.id] = session
            logger.debug(
                f'Сессия пользователя {get_info(member)} отправлена в pending_sessions на {grace_period} сек.')

    # Логика отслеживания сессий общения:
    # === Запуск отслеживания ===
    # Когда в канале оказалось хотя бы 2 подходящих пользователя (не бота)
    # Запускается отслеживание сессий общения для пользователей согласно их разрешениям
    # Для этого в словарь активных сессий сохраняется время начала текущего периода, время начала сессии и ID сервера
    # === Прекращение отслеживания ===
    # При отключении пользователя его сессия общения (при наличии) переносится в список истекающих сессий
    # Через ~3 минуты она будет сохранена в БД и окончательно удалена
    # Для этого сохраняется время истечения сессии и время отключения пользователя от голосового канала
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """### Обработчик изменения голосового канала
        Используется для обработки событий подключения пользователей к голосовым каналам

        Args:
            member (:class:`discord.Member`): Участник сервера, вызвавший событие
            before (:class:`discord.VoiceState`): Состояние голосового канала ДО события
            after (:class:`discord.VoiceState`): Состояние голосового канала ПОСЛЕ события
        """
        logger.debug(
            f'Обработка изменения голосового канала, вызванного {get_info(member)} на сервере {get_info(member.guild)}')
        # Пропускаем ботов
        if member.bot:
            logger.debug('Пользователь - бот, пропуск обработки')
            return
        # Пропускаем события внутри одного канала (мут, включение камеры и т.п.)
        if before.channel == after.channel:
            logger.debug(
                'Канал не изменился (событие не связано с подключением/отключением пользователя), пропуск обработки')
            return

        # Текущий канал изменился (before != after), а значит, пользователь отключился от какого-то канала
        # Обрабатываем отключение пользователя
        if before.channel:
            logger.debug(
                f'Пользователь {get_info(member)} покинул канал {get_info(before.channel)}')
            # Если пользователь не подключился к другому каналу - прекращаем отслеживать его сессию общения
            if after.channel is None:
                await self.stop_tracking(member)
            # Если в канале остался только один валидный пользователь (не бот) - прекращаем отслеживать и его сессию "общения"
            remaining_members = await self.get_valid_voice_members(before.channel)
            if len(remaining_members) < 2:
                logger.debug(
                    f'В канале {get_info(before.channel)} осталось меньше 2 человек. '
                    'Остановка отслеживания времени общения для оставшегося участника (если он есть)')
                for m in remaining_members:
                    await self.stop_tracking(m)

        # Есть данные о новом канале, значит, пользователь подключился к какому-то каналу
        # Обрабатываем подклчючение пользователя
        if after.channel:
            logger.debug(
                f'Пользователь {get_info(member)} подключился к каналу {get_info(after.channel)}')
            # Начинаем сессию общения для всех пользователей в канале, если в канале оказалось хотя бы 2 валидных пользователя (не бота)
            # (Проверяя, что у пользователя ещё нет сессии общения)
            all_channel_members = await self.get_valid_voice_members(after.channel)
            if len(all_channel_members) >= 2:
                logger.debug(
                    f'В канале {get_info(after.channel)} как минимум 2 человека. Запуск отслеживания сессий общения')
                allowed_members = await self.get_valid_voice_members(after.channel, filtered=True)
                for m in allowed_members:
                    await self.start_tracking(m)
            else:
                logger.debug(
                    f'В канале {get_info(after.channel)} меньше 2 человек. Отслеживание сессий общения не требуется')

    @tasks.loop(minutes=15)
    async def save_sessions_task(self):
        """### Задача автосохранения сессий общения
        Автоматически сохраняет данные о сессиях общения пользователей каждые 15 минут
        """
        now = time.time()

        logger.debug('Плановое автосохранение активных сессий...')
        for user_id, data in list(self.active_sessions.items()):
            await self.save_time(user_id, data, True)
            self.active_sessions[user_id]["prd_start"] = now

    @save_sessions_task.before_loop
    async def before_save_sessions(self):
        """### Функция, срабатывающая перед запуском задачи автосохранения сессий
        Ждёт готовности бота до запуска автосохранения, чтобы избежать ошибок при попытке доступа к базе данных до её инициализации
        """
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=30)
    async def delete_pending_sessions_task(self):
        """### Задача удаления истёкших сессий общения
        Автоматически удаляет истёкшие сессии общения и сохраняет их данные в БД
        """
        now = time.time()

        logger.debug('Проверка истекающих сессий...')
        for user_id, data in list(self.pending_sessions.items()):
            logger.debug(f'{user_id} - {int(now)}/{int(data["expires_at"])}')
            if now >= data['expires_at']:
                await self.save_time(user_id, data, False)
                self.pending_sessions.pop(user_id, None)
                logger.debug(
                    f'Льготный период истек. Сессия {user_id} окончательно сохранена в БД')

    @delete_pending_sessions_task.before_loop
    async def before_delete_pending_sessions(self):
        """### Функция, срабатывающая перед запуском задачи удаления истёкших сессий
        Ждёт готовности бота до запуска, чтобы избежать ошибок при попытке доступа к базе данных до её инициализации
        """
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Events(bot))
