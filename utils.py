import os
import logging
from dotenv import load_dotenv

# Инициализируем логгер для этого модуля
logger = logging.getLogger('slashy.utils')
# Загружаем переменные окружения из .env файла
load_dotenv()


def get_env(key: str) -> str:
    """Получает значение переменной окружения из .env файла. Если переменная не найдена, выбрасывает исключение

    Args:
        key (str): Название переменной окружения (например, 'DISCORD_TOKEN')

    Raises:
        ValueError: Если переменная не найдена в .env файле, выбрасывает исключение с сообщением об ошибке

    Returns:
        str: Значение переменной окружения
    """
    value = os.getenv(key)
    if not value:
        logger.error(f"Переменная {key} не задана в .env")
        raise ValueError(
            f"Переменная {key} не задана в .env")
    return value
