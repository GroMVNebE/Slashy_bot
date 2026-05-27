import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import TYPE_CHECKING, Literal
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
                label='Обработка событий', description='Перезагрузить модуль, отвечающий за обработку событий (cogs/events.py)', value='events'
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
     'name': 'Настройки сервера доступны только владельцу',
     'type': 'boolean',
     'choices': [{'name': 'Только владельцу', 'value': True},
                 {'name': 'Администраторам', 'value': False}]},
    {'code': 'vc_stats_enabled',
     'name': 'Сбор статистики времени "общения"',
     'type': 'boolean',
     'choices': [{'name': 'Включен', 'value': True},
                 {'name': 'Отключен', 'value': False}]
     },
]
"""### Список с настройками сервера
Содержит настройки сервера в виде словарей с полями:
- **'code'** - *код настройки, соответствующий столбцу в БД*
- **'name'** - *название настройки для отображения*
- **'type'** - *тип хранимого значения*
- **'choices'** - *возможные значения ('name': str - название значения, 'value': Any - возможное значение)*"""

user_settings = [
    {'code': 'vc_stats_enabled',
     'name': 'Сбор статистики времени "общения"',
     'type': 'boolean',
     'choices': [{'name': 'Включен', 'value': True},
                 {'name': 'Отключен', 'value': False}]
     },
    {'code': 'vc_stats_privacy',
     'name': 'Доступ к статистике времени "общения"',
     'type': 'boolean',
     'choices': [{'name': 'Всем', 'value': True},
                 {'name': 'Никому', 'value': False}]}
]
"""### Список с пользовательскими
Содержит пользовательские настройки в виде словарей с полями:
- **'code'** - *код настройки, соответствующий столбцу в БД*
- **'name'** - *название настройки для отображения*
- **'type'** - *тип хранимого значения*
- **'choices'** - *возможные значения ('name': str - название значения, 'value': Any - возможное значение)*"""


class SettingChoiceButton(discord.ui.Button):
    """### Кнопка выбора конкретного значения настройки
    Создана для установки заранее определенного значения из списка choices"""

    def __init__(self, bot: 'Bot', setting: dict, choice_data: dict, category: Literal['user', 'server'], to_upd: 'ManageSettings'):
        """
        ### Кнопка выбора конкретного значения настройки
        Создана для установки заранее определенного значения из списка choices
        Args:
            bot (:class:`Bot`): Запущенный Дискорд-бот
            setting (dict): Настройка, значение которой изменяем. Является одной из :data:`server_settings` или :data:`user_settings`
            choice_data (dict): Возможное значение настройки, является одним из choices в **setting**. Содержит читаемое название и само значение
            category (Literal['user', 'server']): Категория настройки (настройка сервера или пользовательская настройка)
            to_upd (:class:`ManageSettings`): View, который отображает значения настроек. Хранится для последующего обновления после изменения значения настройки
        """
        super().__init__(label=choice_data['name'],
                         style=discord.ButtonStyle.secondary)
        self.bot = bot
        self.setting = setting
        self.choice_value = choice_data['value']
        self.choice_name = choice_data['name']
        self.category = category
        self.to_upd = to_upd

    async def callback(self, interaction: discord.Interaction):
        logger.debug(
            f'Пользователь {user_data(interaction)} выбрал опцию {self.choice_name} для {self.setting["code"]}')

        if not self.bot.db_pool:
            return

        if self.category == 'user':
            table, where = "user_settings", "guild_id = $2 AND user_id = $3"
            args = (interaction.guild_id, interaction.user.id)
        else:
            table, where = "guild_settings", "guild_id = $2"
            args = (interaction.guild_id,)

        try:
            async with self.bot.db_pool.acquire() as con:
                await con.execute(
                    f"UPDATE {table} SET {self.setting['code']} = $1 WHERE {where}",
                    self.choice_value, *args
                )

            embed = create_embed(
                title=f'Изменение значения параметра {self.setting["name"]}',
                description=f'Новое значение установлено: **{self.choice_name}**',
                color=discord.Color.blurple(),
            )

            await interaction.response.edit_message(embed=embed, view=self.view)
            await self.to_upd.draw_page(None, True)

        except Exception as e:
            logger.error(
                f'Ошибка при установке значения {self.choice_value} для {self.setting["code"]}: {e}', exc_info=True)
            embed = create_embed(
                title='Ошибка!',
                description='Произошла ошибка при попытке изменить значение параметра',
                color=discord.Color.red(),
            )
            await interaction.response.edit_message(embed=embed)


class ChangeSetting(discord.ui.View):
    """### UI-представление для изменения значения настройки
    Содержит несколько :class:`SettingChoiceButton` для выбора значения настройки"""

    def __init__(self, bot: 'Bot', setting: dict, category: Literal['user', 'server'], to_upd: 'ManageSettings', interaction: discord.Interaction):
        """### UI-представление для изменения значения настройки
        Содержит несколько :class:`SettingChoiceButton` для выбора значения настройки
        Args:
            bot (:class:`Bot`): Запущенный Дискорд-бот
            setting (dict): Настройка, значение которой изменяем. Является одной из :data:`server_settings` или :data:`user_settings`
            category (Literal['user', 'server']): Категория настройки (настройка сервера или пользовательская настройка)
            to_upd (:class:`ManageSettings`): View, который отображает значения настроек. Хранится для последующего обновления после изменения значения настройки
            interaction (:class:`discord.Interaction`): Взаимодействие, из которого было вызвано изменение значения настройки
        """
        super().__init__(timeout=60)
        self.bot = bot
        self.setting = setting
        self.category: Literal['user', 'server'] = category
        self.init_view: 'ManageSettings' = to_upd
        self.initial_interaction = interaction

        if 'choices' in self.setting:
            for choice in self.setting['choices']:
                self.add_item(SettingChoiceButton(
                    self.bot, self.setting, choice, self.category, self.init_view
                ))
        else:
            pass

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
    """### Выпадающий список для выбора настройки, значение которой требуется изменить
    Содержит настройки, отображаемые на текущей странице :class:`ManageSettings`, который содержит данный список"""

    def __init__(self, bot: 'Bot', settings: list, category: Literal['user', 'server'], to_upd: 'ManageSettings'):
        """### Выпадающий список для выбора настройки, значение которой требуется изменить
        Содержит настройки, отображаемые на текущей странице :class:`ManageSettings`, который содержит данный список
        Args:
            bot (:class:`Bot`): Запущенный Дискорд-бот
            settings (list[dict]): Список настроек, значение которых можно изменить. Содержит настройки из :data:`server_settings` или :data:`user_settings`
            category (Literal['user', 'server']): Категория настройки (настройка сервера или пользовательская настройка)
            to_upd (:class:`ManageSettings`): View, который отображает значения настроек. Хранится для последующего обновления после изменения значения настройки
        """
        self.bot = bot
        self.category: Literal['user', 'server'] = category
        self.settings = settings
        self.init_view: 'ManageSettings' = to_upd
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

        embed = create_embed(
            title=f'Изменение значения параметра {selected["name"]}',
            description='Выберите новое значение параметра',
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ManageSettings(discord.ui.View):
    """### UI-представление для изменения настроек в одной из категорий
    Предназначено для редактирования настроек сервера :data:`server_settings` или пользовательских настроек :data:`user_settings`"""

    def __init__(self, bot: 'Bot', category: Literal['user', 'server']):
        """### UI-представление для изменения настроек в одной из категорий
        Предназначено для редактирования настроек сервера :data:`server_settings` или пользовательских настроек :data:`user_settings`
        Args:
            bot (:class:`Bot`): Запущенный Дискорд-бот
            category (Literal['user', 'server']): Категория настройки (настройка сервера или пользовательская настройка)
        """
        super().__init__()
        logger.debug('Создание интерфейса для просмотра текущих параметров')
        self.bot = bot
        self.category: Literal['user', 'server'] = category
        self.settings = server_settings if self.category == 'server' else user_settings
        self.pages = []
        self.cur_page = 0
        logger.debug('Генерация страниц с настройками')
        idx = 1
        while idx <= len(self.settings):
            page = []
            while idx % 5 != 0 and idx <= len(self.settings):
                page += [self.settings[idx-1]]
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
        if not interaction.guild or not type(interaction.user) is discord.Member:
            return

        logger.debug(
            f'Начало отрисовки страницы {self.cur_page + 1}/{len(self.pages)} с настройками')

        try:
            logger.debug('Создание заголовка страницы')
            head = 'Редактирование настроек '
            head += f'пользователя {interaction.user.display_name}' if self.category == 'user' else f'сервера {interaction.guild.name}'

            descr = ''
            current_page_settings = self.pages[self.cur_page]

            columns_str = ", ".join([setting['code']
                                    for setting in current_page_settings])

            async with self.bot.db_pool.acquire() as con:
                if self.category == 'user':
                    row = await con.fetchrow(
                        f"SELECT {columns_str} FROM user_settings WHERE guild_id = $1 AND user_id = $2",
                        interaction.guild_id, interaction.user.id
                    )
                    if row is None:
                        await create_default_user_settings(self.bot, interaction.user)
                        row = await con.fetchrow(
                            f"SELECT {columns_str} FROM user_settings WHERE guild_id = $1 AND user_id = $2",
                            interaction.guild_id, interaction.user.id
                        )
                else:
                    row = await con.fetchrow(
                        f"SELECT {columns_str} FROM guild_settings WHERE guild_id = $1",
                        interaction.guild_id
                    )
                    if row is None:
                        await create_default_guild_settings(self.bot, interaction)
                        row = await con.fetchrow(
                            f"SELECT {columns_str} FROM guild_settings WHERE guild_id = $1",
                            interaction.guild_id
                        )

                for setting in current_page_settings:
                    value = row[setting['code']]
                    logger.debug(
                        f'Перевод значения ({value}) в удобный для чтения формат для {setting["code"]}')

                    if 'choices' in setting:
                        display_name = "Не задано"
                        for choice in setting['choices']:
                            if choice['value'] == value:
                                display_name = choice['name']
                                break
                        descr += f'**{setting["name"]}:** ***{display_name}***\n'
                    else:
                        descr += f'**{setting["name"]}:** ***{value if value is not None else "Не задано"}***\n'

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
                self.bot, current_page_settings, self.category, self)
            self.add_item(self.select)

            if update:
                await self.last_interaction.edit_original_response(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
                self.last_interaction = interaction

        except Exception as e:
            logger.error(
                f'Ошибка при отрисовке страницы с настройками категории {self.category} для пользователя '
                f'{user_data(interaction)} на сервере {server_data(interaction)}: {e}', exc_info=True)
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
    """### Кнопка для перехода к изменению пользовательских настроек :data:`user_settings`"""

    def __init__(self, bot: 'Bot'):
        super().__init__(label='Пользовательские настройки')
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        logger.debug(
            f'Пользователь {user_data(interaction)} выбрал категорию "Пользовательские настройки"')
        view = ManageSettings(self.bot, 'user')
        await view.draw_page(interaction)


class ServerSettingsButton(discord.ui.Button):
    """### Кнопка для перехода к изменению настроек сервера :data:`server_settings`"""

    def __init__(self, bot: 'Bot'):
        super().__init__(label='Настройки сервера')
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        logger.debug(
            f'Пользователь {user_data(interaction)} выбрал категорию "Настройки сервера"')
        view = ManageSettings(self.bot, 'server')
        await view.draw_page(interaction)


class SetupView(discord.ui.View):
    """### UI-представление для изменения настроек бота
    Позволяет пользователю перейти к изменению настроек сервера или пользовательских настроек"""

    def __init__(self, bot: 'Bot', interaction: discord.Interaction, allow_server_settings: bool):
        """### UI-представление для изменения настроек бота
        Позволяет пользователю перейти к изменению настроек сервера или пользовательских настроек"""
        super().__init__(timeout=90)
        logger.debug('Создание интерфейса для выбора категории настроек')
        self.bot = bot
        self.initial_interaction = interaction

        if allow_server_settings:
            self.add_item(ServerSettingsButton(bot))
            logger.debug(
                'Пользователю открыт доступ к категории "Управление сервером"')

        self.add_item(UserSettingsButton(bot))
        logger.debug('Интерфейс выбора категории настроек создан')

    @classmethod
    async def create(cls, bot: 'Bot', interaction: discord.Interaction):
        if not interaction.guild or not interaction.guild.owner or not isinstance(interaction.user, discord.Member):
            return cls(bot, interaction, allow_server_settings=False)

        user = interaction.user
        guild = interaction.guild

        is_admin_or_manager = user.guild_permissions.administrator or user.guild_permissions.manage_guild
        is_owner = user.id == guild.owner_id

        allow_server_settings = False

        if is_owner:
            allow_server_settings = True
        elif is_admin_or_manager:
            only_owner_access = False
            if bot.db_pool:
                try:
                    async with bot.db_pool.acquire() as con:
                        row = await con.fetchrow(
                            "SELECT only_owner_access FROM guild_settings WHERE guild_id = $1",
                            guild.id
                        )
                        if row is not None:
                            only_owner_access = row['only_owner_access']
                except Exception as e:
                    logger.error(
                        f'Ошибка при проверке настройки only_owner_access для гильдии {guild.id}: {e}', exc_info=True)

            if not only_owner_access:
                allow_server_settings = True

        return cls(bot, interaction, allow_server_settings)

    async def on_timeout(self):
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

    - Команда :meth:`manage` для управления ботом
    - Команда :meth:`setup` для изменения настроек бота"""

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

        await interaction.response.defer(ephemeral=True)

        embed = create_embed(
            title='Выберите нужную категорию, которую хотите настроить',
            color=discord.Color.blue()
        )
        view = await SetupView.create(self.bot, interaction)
        await interaction.edit_original_response(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(Admin(bot))
