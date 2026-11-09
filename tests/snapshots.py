"""### Снимочные (snapshot) тесты картинок
Картинка сравнивается с эталоном из tests/snapshots/<name>.png с допуском: размер должен совпадать точно,
а средняя разница пикселей - не превышать порог (сглаживание шрифтов на другой ОС не роняет тест,
а сдвинутый текст, другие цифры или цвета - роняют).

Создать или обновить эталоны: `UPDATE_SNAPSHOTS=1 pytest ...` (каждый новый эталон нужно проверить глазами)"""
import io
import os
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

SNAPSHOTS_DIR = Path(__file__).parent / 'snapshots'
FAILED_DIR = SNAPSHOTS_DIR / 'failed'


CHANGED_PIXEL_THRESHOLD = 64
"""### Разница канала (0-255), начиная с которой пиксель считается изменённым, а не отличающимся сглаживанием"""


def get_diff_stats(first: Image.Image, second: Image.Image) -> tuple[float, float]:
    """### Функция для сравнения двух картинок одного размера

    Returns:
        tuple[float, float]: (средняя разница пикселей по шкале 0-255, доля заметно изменённых пикселей)
    """
    diff = ImageChops.difference(first.convert('RGB'), second.convert('RGB'))
    channels = ImageStat.Stat(diff).mean
    # Пиксель изменён, если хотя бы в одном канале разница больше порога
    per_channel = [band.point(lambda v: 255 if v > CHANGED_PIXEL_THRESHOLD else 0) for band in diff.split()]
    changed = ImageChops.lighter(ImageChops.lighter(per_channel[0], per_channel[1]), per_channel[2])
    changed_ratio = ImageStat.Stat(changed).mean[0] / 255
    return sum(channels) / len(channels), changed_ratio


def assert_image_matches(png: bytes | io.BytesIO, name: str, max_mean_diff: float = 2.0,
                         max_changed_ratio: float = 0.0001) -> None:
    """### Функция для сравнения картинки с эталоном
    Средняя разница ловит крупные изменения (цвета, фон), доля изменённых пикселей - мелкие
    (другие цифры, сдвинутый текст), не реагируя на сглаживание шрифтов

    Args:
        png (bytes | io.BytesIO): Проверяемая картинка в формате PNG
        name (str): Имя эталона без расширения
        max_mean_diff (float, optional): Допустимая средняя разница пикселей (0-255)
        max_changed_ratio (float, optional): Допустимая доля заметно изменённых пикселей (по умолчанию 0.01%: одна изменённая цифра на карточке /lvl - около 0.03%)
    """
    data = png.getvalue() if isinstance(png, io.BytesIO) else png
    snapshot = SNAPSHOTS_DIR / f'{name}.png'
    if os.getenv('UPDATE_SNAPSHOTS') == '1':
        SNAPSHOTS_DIR.mkdir(exist_ok=True)
        snapshot.write_bytes(data)
        return
    if not snapshot.exists():
        pytest.fail(f'Нет эталона {snapshot}. Создайте его через UPDATE_SNAPSHOTS=1 и проверьте глазами')

    actual = Image.open(io.BytesIO(data))
    expected = Image.open(snapshot)
    problem = None
    if actual.size != expected.size:
        problem = f'размер {actual.size} вместо {expected.size}'
    else:
        mean_diff, changed_ratio = get_diff_stats(actual, expected)
        if mean_diff > max_mean_diff:
            problem = f'средняя разница пикселей {mean_diff:.2f} > {max_mean_diff}'
        elif changed_ratio > max_changed_ratio:
            problem = f'изменено {changed_ratio:.3%} пикселей > {max_changed_ratio:.3%}'
    if problem:
        FAILED_DIR.mkdir(parents=True, exist_ok=True)
        (FAILED_DIR / f'{name}.png').write_bytes(data)
        pytest.fail(f'Картинка не совпадает с эталоном {name}: {problem}. '
                    f'Фактическая картинка: {FAILED_DIR / f"{name}.png"}')
