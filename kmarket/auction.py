"""Снимок товарного аукциона (commodities) и его свёртка в цену предмета.

Ноль зависимостей: модуль работает внутри облачного сборщика, а тому
`pip install` противопоказан (лишняя минута и лишняя точка отказа на каждом
запуске).

ЧТО ЭТО ЗА ДАННЫЕ. Эндпоинт `/data/wow/auctions/commodities` отдаёт
расходники и реагенты сразу по ВСЕМУ региону — реалм для них не важен.
Обновляется РАЗ В ЧАС, около 41-й минуты, у всех одинаково: у нас, у TSM,
у любых сайтов. Быстрее рынок видно только из игры. Для торговли циклом
патча это не мешает (цена между часовыми снимками сдвигается на 0.3%),
а для перекупки «увидел дешёвый лот — выкупил» не годится вовсе — эта
ветка проекта закрыта, см. CLAUDE.md.

ПОЧЕМУ ДВЕ ЦЕНЫ, А НЕ ОДНА.

* `floor`  — самый дешёвый лот. Это цена, которую видишь в игре первой.
* `market` — цена на границе нижних 15% предложения. Устойчива к
             одиночным выбросам: один лот по бросовой цене двигает пол, но
             не двигает её. Вся аналитика строится по ней.

Средняя цена не годится: на аукционе всегда висят одиночные лоты по
конской цене, и они тянут среднее туда, где никто не покупает.

КОЛИЧЕСТВУ НЕ ВЕРИМ (замерено 2026-08-08): между часовыми снимками цена
дрейфует на 0.3%, а количество — на 19.8%. Поэтому `quantity` пишется как
справка о глубине рынка, но ни один вывод на нём не строится.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from . import blizzard

# Доля предложения, по которой считаем «рыночную» цену. 15% отсекают
# одиночные дешёвые выбросы снизу и не уезжают в середину стакана, откуда
# никто не покупает.
MARKET_SHARE = 0.15


@dataclass(frozen=True)
class Quote:
    """Цена одного предмета в снимке."""

    item_id: int
    floor: int  # медь за единицу, самый дешёвый лот
    market: int  # медь за единицу, граница нижних 15% предложения
    quantity: int  # всего единиц выставлено — справка, не основание для выводов


@dataclass(frozen=True)
class Snapshot:
    region: str
    updated: datetime  # время САМОЙ Blizzard, ключ дедупликации
    quotes: dict[int, Quote]


def _fold(book: list[tuple[int, int]]) -> tuple[int, int, int]:
    """Стакан [(цена, количество), ...] → (floor, market, quantity)."""
    book.sort()
    total = sum(quantity for _, quantity in book)
    target = total * MARKET_SHARE
    seen = 0
    market = book[0][0]
    for price, quantity in book:
        market = price
        seen += quantity
        if seen >= target:
            break
    return book[0][0], market, total


def fetch(region: str, access_token: str) -> Snapshot:
    """Снимок товарного аукциона региона.

    Ответ большой (2.9 МБ сжатых, 24.8 МБ распакованных), поэтому идёт с
    длинным таймаутом: тридцати секунд ему мало.
    """
    url = (
        f"https://{region}.api.blizzard.com/data/wow/auctions/commodities"
        f"?namespace=dynamic-{region}"
    )
    data, updated = blizzard._http_json_dated(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=blizzard.BIG_TIMEOUT,
    )

    books: dict[int, list[tuple[int, int]]] = {}
    for lot in data.get("auctions", ()):
        try:
            item_id = int(lot["item"]["id"])
            price = int(lot["unit_price"])
            quantity = int(lot["quantity"])
        except (KeyError, TypeError, ValueError):
            continue  # битый лот не повод терять весь снимок
        if quantity > 0 and price > 0:
            books.setdefault(item_id, []).append((price, quantity))

    if not books:
        raise RuntimeError(f"Снимок аукциона {region.upper()} пуст — это не норма")

    return Snapshot(
        region=region,
        # Заголовка может не быть — тогда датируем моментом опроса. Хуже,
        # чем время Blizzard (дедупликация станет неточной), но лучше, чем
        # уронить сбор из-за отсутствующего заголовка.
        updated=(updated or datetime.now(timezone.utc)).replace(microsecond=0),
        quotes={item_id: Quote(item_id, *_fold(book)) for item_id, book in books.items()},
    )
