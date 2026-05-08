import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import TYPE_CHECKING, Any, Literal
from utils import create_embed, get_env, user_data, server_data, create_default_user_settings, create_default_guild_settings

# Подлючаем типизацию для класса Bot из launch.py, избегая циклического импорта
# Нужно для правильного определения типов в IDE
if TYPE_CHECKING:
    from launch import Bot

# Инициализируем логгер для этого модуля
logger = logging.getLogger('slashy.admin')

# Получаем ID разработчика из окружения для проверки прав
CREATOR_ID = int(get_env('CREATOR_ID'))


class ModuleSelect(discord.ui.Select):
    """### Выпадающий список для выбора модуля на перезагрузку
    Содержит варианты выбора модулей для перезагрузки,
    после выбора перезагружает модуль и синхронизирует команды"""

    def __init__(self, bot: 'Bot'):
        """
        ### Выпадающий список для выбора модуля на перезагрузку
        Содержит варианты выбора модулей для перезагрузки,
        после выбора перезагружает модуль и синхронизирует команды
        Args:
            bot (:class:`Bot`): Запущенный Дискорд-бот
        """
        logger.debug('Инициализация меню выбора класса ModuleSelect')
        self.bot: Bot = bot
        # Добавляем варианты выбора модулей для перезагрузки
        # value должен соответствовать формату "cog_name" где "cogs/cog_name.py" - файл кога
        # пр. "general" для "cogs/general.py"
        options = [
            discord.SelectOption(
                label='Общие команды', description='Перезагрузить модуль с общими командами (cogs/general.py)', value='general'),
            discord.SelectOption(
                label='Команды управления', description='Перезагрузить модуль с командами управления ботом (cogs/admin.py)', value='admin'),
            discord.SelectOption(
                label='Обработка событий', description='Перезагрузить модуль, отвечающий за обработку событий (cogs/event.py)', value='event'
            )
        ]
        # Используем конструктор родительского класса, указывая ограничения
        super().__init__(placeholder='Выберите модуль для перезагрузки...',
                         min_values=1, max_values=1, options=options)
        logger.debug(
            'Завершена инициализация меню выбора класса ModuleSelect')

    async def callback(self, interaction: discord.Interaction):
        logger.debug(
            f'Пользователь {user_data(interaction)} выбрал {self.values[0]} в ModuleSelect')
        # Получаем имя выбранного модуля и формируем путь к нему
        module_name = self.values[0]
        cog_path = f'cogs.{module_name}'
        logger.info(f'Была начата перезагрузка модуля {module_name}')
        # Отправляем ответ, показывающий, что бот обрабатывает запрос (может занять некоторое время)
        await interaction.response.defer(ephemeral=True)
        # Пробуем перезагрузить выбранный модуль и синхронизировать команды
        try:
            # Перезагружаем модуль
            logger.debug('Выполняется перезагрузка кога')
            await self.bot.reload_extension(cog_path)
            # Синхронизируем команды после перезагрузки
            # (На случай, если были добавлены/удалены команды или изменены их параметры)
            logger.debug('Выполняется синхронизация дерева команд')
            await self.bot.tree.sync()
            # Отправляем сообщение об успешной перезагрузке
            logger.debug('Отправка сообщения об успешной перезагрузке')
            embed = create_embed(
                title='Перезагрузка модуля завершена',
                description=f'Модуль **{module_name}** был успешно перезагружен. В случае, если были добавлены/удалены команды, или изменены их параметры, изменения должны вступить в силу через некоторое время',
                color=discord.Color.green()
            )
            await interaction.edit_original_response(embed=embed, view=None)
            logger.info(f'Модуль {cog_path} перезагружен')
        # Если произошла ошибка при перезагрузке, логируем её и отправляем сообщение об ошибке
        except Exception as e:
            logger.error(
                f'Ошибка перезагрузки модуля {cog_path}: {e}', exc_info=True)
            embed = create_embed(
                title='Прозошла ошибка',
                description=f'В процессе перезагрузки модуля **{module_name}** произошла ошибка. Проверьте логи для получения подробной информации',
                color=discord.Color.red()
            )
            await interaction.edit_original_response(embed=embed, view=None)


class ModuleReloadView(discord.ui.View):
    """### UI-представление для выбора модуля на перезагрузку
    Содержит в себе выпадающий список :class:`ModuleSelect` для выбора модуля на перезагрузку"""

    def __init__(self, bot: 'Bot'):
        """
        ### UI-представление для выбора модуля на перезагрузку
        Содержит в себе выпадающий список :class:`ModuleSelect` для выбора модуля на перезагрузку
        Args:
            bot (:class:`Bot`): Запущенный Дискорд-бот
        """
        logger.debug('Инициализация интерфейса из класса ModuleReloadView')
        super().__init__()
        # Создаём выпадающий список и добавляем его к текущему View
        self.add_item(ModuleSelect(bot))
        logger.debug(
            'Завершена инициализация интерфейса из класса ModuleReloadView')


class ActionSelect(discord.ui.Select):
    """### Выпадающий список для выбора действия в панели управления
    Содержит варианты "Выключить бота" и "Перезагрузить модуль", после выбора выполняет соответствующее действие"""

    def __init__(self, bot: 'Bot'):
        """
        ### Выпадающий список для выбора действия в панели управления
        Содержит варианты "Выключить бота" и "Перезагрузить модуль", после выбора выполняет соответствующее действие
        Args:
            bot (:class:`Bot`): Запущенный Дискорд-бот
        """
        logger.debug('Инициализация меню выбора класса ActionSelect')
        self.bot = bot
        # Добавляем варианты выбора действий
        options = [
            discord.SelectOption(
                label='Выключить бота', description='Прекращает работу бота', value='shutdown', emoji='🛑'),
            discord.SelectOption(label='Перезагрузить модуль',
                                 description='Перезагружает выбранный ког', value='reload', emoji='⚙️')
        ]
        # Используем конструктор родительского класса, указывая ограничения
        super().__init__(placeholder='Выберите действие...',
                         min_values=1, max_values=1, options=options)
        logger.debug(
            'Завершена инициализация меню выбора класса ActionSelect')

    async def callback(self, interaction: discord.Interaction):
        logger.debug(
            f'Пользователь {user_data(interaction)} выбрал {self.values[0]} в ActionSelect')
        # Получаем выбранное действие
        action = self.values[0]
        # Если нужно выключить бота
        if action == 'shutdown':
            # Отправляем сообщение о выключении бота (до выключения, иначе не сможем отправить)
            embed = create_embed(
                title='Бот успешно выключен',
                description='Соединение с базой данных закрыто, клиент Discord отключён',
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)
            # Логгируем выключение бота
            logger.info(
                f'Пользователь {user_data(interaction)} выключил бота')
            # Закрываем соединения с БД и соединение клиента с Discord
            if self.bot.db_pool:
                await self.bot.db_pool.close()
            await self.bot.close()
        # Если нужно перезагрузить модуль
        elif action == 'reload':
            # Отправляем сообщение с формой выбора модуля для перезагрузки
            embed = create_embed(
                title='Перезагрузка модулей',
                description='Выберите нужный модуль из списка ниже:',
                color=discord.Color.blue()
            )
            await interaction.response.edit_message(embed=embed, view=ModuleReloadView(self.bot))


class ManageView(discord.ui.View):
    """### UI-представление для панели управления ботом
    Содержит в себе выпадающий список :class:`ActionSelect` для выбора действия"""

    def __init__(self, bot: 'Bot', interaction: discord.Interaction):
        """
        ### UI-представление для панели управления ботом
        Содержит в себе выпадающий список :class:`ActionSelect` для выбора действия
        Args:
            bot (:class:`Bot`): Запущенный Дискорд-бот
            interaction (:class:`discord.Interaction`): Начальное взаимодействие, из которого был запущен текущий View. 
                Хранится для удаления сообщения по истечении времени работы View
        """
        logger.debug('Инициализация интерфейса класса ManageView')
        super().__init__(timeout=45)
        self.initial_interaction = interaction
        # Добавляем меню выбора действия к текущему View
        self.add_item(ActionSelect(bot))
        logger.debug('Завершена инициализация интерфейса класса ManageView')

    async def on_timeout(self):
        # По истечении тайм-аута пробуем удалить исходное сообщение
        try:
            logger.debug('Время работы панели управления ботом истекло')
            await self.initial_interaction.delete_original_response()
        except discord.NotFound:
            pass
        except Exception as e:
            logger.error(
                f'Возникла ошибка при удалении панели управления ботом: {e}', exc_info=True
            )


server_settings = [
    {'code': 'only_owner_access',
        'name': 'Настройки сервера доступны только владельцу', 'type': 'bool'},
    {'code': 'vc_stats_enabled',
        'name': 'Сбор статистики времени "общения"', 'type': 'bool'},
]
"""### Список с настройками сервера
Содержит настройки сервера в виде словарей с полями:
- **'code'** - *код настройки, соответствующий столбцу в БД*
- **'name'** - *название настройки для отображения*
- **'type'** - *тип хранимого значения*"""

user_settings = [
    {'code': 'vc_stats_enabled',
        'name': 'Сбор статистики времени "общения"', 'type': 'bool'},
]
"""### Список с пользовательскими
Содержит пользовательские настройки в виде словарей с полями:
- **'code'** - *код настройки, соответствующий столбцу в БД*
- **'name'** - *название настройки для отображения*
- **'type'** - *тип хранимого значения*"""


# (!) Этот класс нужно переписать, чтобы в нём были значения по умолчанию/кнопка для ввода значения,
# Чтобы обеспечить поддержку всех типов настроек
class ToggleSettingButton(discord.ui.Button):
    """### Кнопка переключения настройки
    Создана для изменения значения bool-настройки (*True->False*, *False->True*)"""

    def __init__(self, bot: 'Bot', setting: dict, category: Literal['user', 'server'], to_upd: 'ManageSettings'):
        """
        ### Кнопка переключения настройки
        Создана для изменения значения bool-настройки (*True->False*, *False->True*)
        Args:
            bot (:class:`Bot`): Запущенный Дискорд-бот
            setting (dict): Настройка, значение которой переключаем. Одно из значений :data:`server_settings` или :data:`user_settings`
            category (Literal[&#39;user&#39;, &#39;server&#39;]): Категория настройки (серверная или пользовательская)
            to_upd (:class:`ManageSettings`): Интерфейс, в котором находится список всех значений настроек.
                Будет обновлён при изменении значения настройки
        """
        logger.debug('Инициализация кнопки класса ToggleSettingButton')
        super().__init__(label='Переключить')
        self.bot = bot
        self.setting = setting
        self.category: Literal['user', 'server'] = category
        self.to_upd: 'ManageSettings' = to_upd
        logger.debug(
            'Завершена инициализация кнопки класса ToggleSettingButton')

    async def callback(self, interaction: discord.Interaction):
        logger.debug(
            f'Пользователь {user_data(interaction)} нажал на кнопку ToggleSettingButton')
        # Проверяем, что есть пул соединений с БД
        if not self.bot.db_pool:
            return
        # Пробуем поменять значение настройки
        logger.debug(f'Изменение значения {self.setting["code"]}')
        try:
            async with self.bot.db_pool.acquire() as con:
                # Формируем запрос на получение настройки из БД
                query = f"""
                    SELECT {self.setting['code']}"""
                if self.category == 'user':
                    query += """
                        FROM user_settings
                        WHERE guild_id = $1 AND user_id = $2
                    """
                else:
                    query += """
                        FROM guild_settings
                        WHERE guild_id = $1
                    """
                # Получаем значение с помощью сформированного запроса
                logger.debug(
                    f'Получение текущего значения {self.setting["code"]} из БД')
                row = await con.fetchrow(
                    query,
                    interaction.guild_id,
                    interaction.user.id
                )
                # Вычисляем новое значение настройки
                val = False if row[0] else True
                logger.debug(f'Пользователь {user_data(interaction)} переключил значение параметра для {"Пользовательских настроек" if self.category=="user" else "Настроек сервера"} \
{self.setting["code"]} на сервере {server_data(interaction)}, теперь оно {val} вместо {row[0]}')
                # Формируем запрос на обновление настройки в БД
                if self.category == 'user':
                    query = f"""
                        UPDATE user_settings
                        SET {self.setting['code']} = $1
                        WHERE guild_id = $2 AND user_id = $3
                    """
                else:
                    query = f"""
                        UPDATE guild_settings
                        SET {self.setting['code']} = $1
                        WHERE guild_id = $2
                    """
                # Обновляем значение настройки в БД
                logger.debug(
                    f'Обновление значения {self.setting["code"]} в БД')
                row = await con.execute(
                    query,
                    val,
                    interaction.guild_id,
                    interaction.user.id
                )
                # Обновляем текст на кнопке
                self.label = 'Отключить' if val else 'Включить'
                # Отправляем сообщение с новым значением настройки
                logger.debug(
                    f'Отправка сообщения об изменении настройки {self.setting["code"]}')
                text = f'***Включено*** :white_check_mark:' if val is True else '***Отключено*** :x:'
                embed = create_embed(
                    title=f'Изменение значения параметра {self.setting["name"]}',
                    description=text,
                    color=discord.Color.blurple(),
                )
                await interaction.response.edit_message(embed=embed)
                # Обновляем сообщение со списком настроек, чтобы там отобразилось актуальное значение
                logger.debug(
                    f'Обновление страницы со значениями настроек, среди которых есть изменённая ({self.setting["code"]})')
                await self.to_upd.draw_page(None, True)
        # В случае ошибки - выводим сообщение об ошибке
        except Exception as e:
            logger.error(
                f'Ошибка при изменении значения параметра {self.setting["code"]} для пользователя \
{user_data(interaction)} на сервере {server_data(interaction)}: {e}', exc_info=True)
            embed = create_embed(
                title='Ошибка!',
                description='Произошла ошибка при попытке изменить значение параметра',
                color=discord.Color.red(),
            )
            await interaction.response.edit_message(embed=embed)


class ChangeSetting(discord.ui.View):

    def __init__(self, bot: 'Bot', setting: dict, category: Literal['user', 'server'], view: 'ManageSettings', interaction: discord.Interaction):
        super().__init__(timeout=60)
        self.bot = bot
        self.setting = setting
        self.category: Literal['user', 'server'] = category
        self.init_view: 'ManageSettings' = view
        self.initial_interaction = interaction
        if self.setting['type'] == 'bool':
            self.add_item(ToggleSettingButton(
                self.bot, self.setting, self.category, self.init_view))
        else:
            return

    async def on_timeout(self):
        # По истечении тайм-аута пробуем удалить исходное сообщение
        try:
            logger.debug(
                'Время работы панели выбора категории настроек истекло')
            await self.initial_interaction.delete_original_response()
        except discord.NotFound:
            pass
        except Exception as e:
            logger.error(
                f'Возникла ошибка при удалении панели выбора категории настроек: {e}', exc_info=True
            )


class SelectSetting(discord.ui.Select):

    def __init__(self, bot: 'Bot', settings: list, category: Literal['user', 'server'], view: 'ManageSettings'):
        self.bot = bot
        self.category: Literal['user', 'server'] = category
        self.settings = settings
        self.init_view: 'ManageSettings' = view
        options = [
            discord.SelectOption(
                label=setting['name'], value=setting['code']) for setting in self.settings
        ]
        super().__init__(placeholder='Изменить настройку',
                         min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not self.bot.db_pool:
            return
        selected = None
        for setting in self.settings:
            if setting['code'] == self.values[0]:
                selected = setting
                break
        if not selected:
            return
        view = ChangeSetting(self.bot, selected,
                             self.category, self.init_view, interaction)
        text = ''
        try:
            async with self.bot.db_pool.acquire() as con:
                if self.category == 'user':
                    row = await con.fetchrow(
                        f"""
                        SELECT {selected['code']}
                        FROM user_settings
                        WHERE guild_id = $1 AND user_id = $2
                    """,
                        interaction.guild_id,
                        interaction.user.id
                    )
                else:
                    row = await con.fetchrow(
                        f"""
                        SELECT {selected['code']}
                        FROM guild_settings
                        WHERE guild_id = $1
                    """,
                        interaction.guild_id,
                    )
                    if selected['type'] == 'bool':
                        text = f'**{selected["name"]}**: ' + \
                            '***Включено*** :white_check_mark:' if row[0] is True else '***Отключено*** :x:'
                if selected['type'] == 'bool':
                    text = f'**{selected["name"]}**: ' + \
                        '***Включено*** :white_check_mark:' if row[0] is True else '***Отключено*** :x:'
                embed = create_embed(
                    title=f'Изменение значения параметра {selected["name"]}',
                    description=text,
                    color=discord.Color.blurple(),
                )
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            logger.error(
                f'Ошибка при подготовке текста для изменения пользовательской настройки {selected["code"]} для пользователя \
{user_data(interaction)} на сервере {server_data(interaction)}: {e}', exc_info=True)
            embed = create_embed(
                title='Ошибка!',
                description='Произошла ошибка при создании встраиваемой формы с интерфейсом для изменения значения параметра',
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


class ManageSettings(discord.ui.View):

    def __init__(self, bot: 'Bot', category: Literal['user', 'server']):
        super().__init__()
        logger.debug('Создание интерфейса для просмотра текущих параметров')
        self.bot = bot
        self.category: Literal['user', 'server'] = category
        self.settings = server_settings if self.category == 'server' else user_settings
        self.pages = []
        self.cur_page = 0
        logger.debug('Генерация страниц с настройками')
        idx = 0
        while idx < len(self.settings):
            page_size = 0
            page = []
            while page_size < 1000 and idx < len(self.settings):
                page += [self.settings[idx]]
                page_size += len(self.settings[idx]['name'])
                idx += 1
            self.pages += [page]
        self.select = None

    async def draw_page(self, interaction: discord.Interaction | None, update: bool = False):
        if not self.bot.db_pool:
            return
        if not interaction and self.last_interaction:
            interaction = self.last_interaction
        elif not interaction:
            return
        if not interaction.guild:
            return
        logger.debug(
            f'Начало отрисовки страницы {self.cur_page + 1}/{len(self.pages)} с настройками')
        try:
            logger.debug('Создание заголовка страницы')
            head = 'Редактирование настроек '
            head += f'пользователя {interaction.user.display_name}' if self.category == 'user' else f'сервера \
{interaction.guild.name}'
            logger.debug('Создание содержимого страницы')
            descr = ''
            async with self.bot.db_pool.acquire() as con:
                for setting in self.pages[self.cur_page]:
                    logger.debug(
                        f'Получение значения настройки {setting["code"]} из БД')
                    if self.category == 'user':
                        row = await con.fetchrow(
                            f"""
                            SELECT {setting['code']}
                            FROM user_settings
                            WHERE guild_id = $1 AND user_id = $2
                        """,
                            interaction.guild_id,
                            interaction.user.id
                        )
                        if row is None:
                            await create_default_user_settings(self.bot, interaction)
                            row = await con.fetchrow(
                                f"""
                                SELECT {setting['code']}
                                FROM user_settings
                                WHERE guild_id = $1 AND user_id = $2
                            """,
                                interaction.guild_id,
                                interaction.user.id
                            )
                    else:
                        row = await con.fetchrow(
                            f"""
                            SELECT {setting['code']}
                            FROM guild_settings
                            WHERE guild_id = $1
                        """,
                            interaction.guild_id,
                        )
                        if not row:
                            await create_default_guild_settings(self.bot, interaction)
                            row = await con.fetchrow(
                                f"""
                                SELECT {setting['code']}
                                FROM guild_settings
                                WHERE guild_id = $2
                            """,
                                interaction.guild_id,
                            )
                    value = row[0]
                    logger.debug(
                        f'Перевод значения ({row[0]}) в удобный для чтения формат (тип: {setting["type"]})')
                    if setting['type'] == 'bool':
                        descr += f'**{setting["name"]}:** {"***Включено*** :white_check_mark:" if value is True else "***Отключено*** :x:"}\n'
                        logger.debug(
                            f'Описание для настройки {setting["code"]} получено')
                    else:
                        logger.error(
                            f'Обнаружена настройка с неизвестным типом {setting["type"]}')
                        embed = create_embed(
                            title='Ошибка!',
                            description='Произошла ошибка при отрисовке страницы с настройками',
                            color=discord.Color.red(),
                        )
                        if update:
                            await self.last_interaction.edit_original_response(
                                embed=embed, view=None)
                        else:
                            await interaction.response.edit_message(embed=embed)
            logger.debug(
                f'Обновление страницы {self.cur_page+1}/{len(self.pages)} с настройками')
            embed = create_embed(
                title=head,
                description=descr,
                color=discord.Color.blue(),
                footer_text=f'Страница {self.cur_page+1}/{len(self.pages)}'
            )
            if self.select:
                self.remove_item(self.select)
            self.select = SelectSetting(
                self.bot, self.pages[self.cur_page], self.category, self)
            self.add_item(self.select)
            if update:
                await self.last_interaction.edit_original_response(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
                self.last_interaction = interaction
        except Exception as e:
            logger.error(
                f'Ошибка при отрисовке страницы с настройками категории {self.category} для пользователя \
{user_data(interaction)} на сервере {server_data(interaction)}: {e}', exc_info=True)
            embed = create_embed(
                title='Ошибка!',
                description='Произошла ошибка при отрисовке страницы с настройками',
                color=discord.Color.red(),
            )
            if update:
                await self.last_interaction.edit_original_response(embed=embed, view=None)
            else:
                await interaction.response.edit_message(embed=embed)

    @discord.ui.button(emoji='⬅️')
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.cur_page > 0:
            self.cur_page -= 1
        else:
            self.cur_page = len(self.pages)-1
        logger.debug(
            f'Возврат к предыдущей ({self.cur_page+1}/{len(self.pages)}) странице настроек')
        await self.draw_page(interaction)

    @discord.ui.button(emoji='➡️')
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.cur_page < len(self.pages)-1:
            self.cur_page += 1
        else:
            self.cur_page = 0
        logger.debug(
            f'Переход к следующей ({self.cur_page+1}/{len(self.pages)}) странице настроек')
        await self.draw_page(interaction)


class UserSettingsButton(discord.ui.Button):

    def __init__(self, bot: 'Bot'):
        super().__init__(label='Пользовательские настройки')
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        logger.debug(
            f'Пользователь {user_data(interaction)} выбрал категорию "Пользовательские настройки"')
        view = ManageSettings(self.bot, 'user')
        await view.draw_page(interaction)


class ServerSettingsButton(discord.ui.Button):

    def __init__(self, bot: 'Bot'):
        super().__init__(label='Настройки сервера')
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        logger.debug(
            f'Пользователь {user_data(interaction)} выбрал категорию "Настройки сервера"')
        view = ManageSettings(self.bot, 'server')
        await view.draw_page(interaction)


class SetupView(discord.ui.View):
    """### UI-представление для настройки бота"""

    def __init__(self, bot: 'Bot', interaction: discord.Interaction):
        super().__init__(timeout=90)
        logger.debug('Создание интерфейса для выбора категории настроек')
        if not interaction.guild or not interaction.guild.owner or not type(interaction.user) is discord.Member:
            return
        logger.debug('Проверка категории "Управление сервером"')
        if interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner.id or interaction.user.guild_permissions.manage_guild:
            self.add_item(ServerSettingsButton(bot))
            logger.debug(
                'Пользователю открыт доступ к категории "Управление сервером"')
        self.add_item(UserSettingsButton(bot))
        self.initial_interaction = interaction
        logger.debug('Интерфейс выбора категории настроек создан')

    async def on_timeout(self):
        # По истечении тайм-аута пробуем удалить исходное сообщение
        try:
            logger.debug(
                'Время работы панели выбора категории настроек истекло')
            await self.initial_interaction.delete_original_response()
        except discord.NotFound:
            pass
        except Exception as e:
            logger.error(
                f'Возникла ошибка при удалении панели выбора категории настроек: {e}', exc_info=True
            )


class Admin(commands.Cog):
    """### Модуль с административными командами для управления/настройки бота

    - Команда :meth:`manage` для управления ботом"""

    def __init__(self, bot: 'Bot'):
        self.bot = bot

    @app_commands.command(name="manage", description="Панель управления ботом (только для разработчика)")
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id, i.user.id))
    async def manage(self, interaction: discord.Interaction):
        logger.info(
            f'Пользователь {interaction.user.id} ({interaction.user.display_name}) попытался вызвать панель управления ботом')
        # Проверяем, является ли пользователь разработчиком
        if interaction.user.id != CREATOR_ID:
            logger.warning(
                f'Пользователь {interaction.user.display_name} попытался получить доступ к панели управления ботом, но у него нет на это прав')
            embed = create_embed(
                title='У Вас нет доступа к этой команде',
                description='Данная команда доступна только разработчику, оставьте ему эту работу ◑﹏◐',
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        # Создаём интерфейс панели управления и отправляем его пользователю
        logger.info('Запущена панель управления ботом')
        embed = create_embed(
            title='Панель управления Slashy',
            description='Выберите нужное действие из выпадающего списка ниже',
            color=discord.Color.blue()
        )
        view = ManageView(self.bot, interaction)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="setup", description="Настройки бота")
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id, i.user.id))
    async def setup(self, interaction: discord.Interaction):
        logger.info(
            f'Пользователь {user_data(interaction)} вызвал настройки бота на сервере {server_data(interaction)}')
        embed = create_embed(
            title='Выберете нужную категорию, которую хотите настроить',
            color=discord.Color.blue()
        )
        view = SetupView(self.bot, interaction)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
