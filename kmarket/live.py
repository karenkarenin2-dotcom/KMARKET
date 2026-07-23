"""Живой запрос текущей цены — для свежести дашборда.

ЗАЧЕМ ОТДЕЛЬНО ОТ СБОРЩИКА. Сборщик (collect.py) пишет историю в облаке
каждые 10 минут. Дашборд же читает файлы с диска, и между `git pull` они
устаревают. Этот модуль закрывает разрыв с другой стороны: при открытии
страницы дашборд сам спрашивает у Blizzard текущую цену, показывает её в
заголовке и заодно дописывает точку в локальную историю (дедуп по времени
Blizzard защищает от повторов). Так цена в шапке всегда актуальна до
минуты, даже если историю давно не подтягивали.

НИКОГДА НЕ РОНЯЕТ ДАШБОРД. Нет ключей, нет интернета, Blizzard молчит —
функция возвращает None, и дашборд показывает последнюю цену из истории,
честно пометив её возраст. Живая цена — улучшение, а не зависимость.

Access-токен кэшируется в процессе: у него сутки жизни, дёргать OAuth на
каждое обновление страницы незачем.
"""

from __future__ import annotations

import time

from . import storage
from .blizzard import TokenPrice, fetch_token_price, get_access_token

_token: str | None = None
_token_expires: float = 0.0
TOKEN_TTL = 3600  # берём с большим запасом: реально токен живёт сутки


def _access_token() -> str:
    global _token, _token_expires
    if _token is None or time.monotonic() > _token_expires:
        _token = get_access_token()
        _token_expires = time.monotonic() + TOKEN_TTL
    return _token


def current_price(region: str) -> TokenPrice | None:
    """Текущая цена региона напрямую у Blizzard. None — если не вышло."""
    try:
        price = fetch_token_price(region, _access_token())
    except Exception:  # noqa: BLE001 — свежесть необязательна, молчим и живём дальше
        return None
    # Дописываем в локальную историю: открытие дашборда = ещё одна точка.
    try:
        storage.append(price)
    except OSError:
        pass  # не смогли записать — не беда, показать цену это не мешает
    return price
