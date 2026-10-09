"""Снимок аукционных товаров (commodities) и его свёртка в показатели.

Ноль зависимостей: этот модуль работает внутри облачного сборщика, а тому
`pip install` противопоказан (лишняя минута и лишняя точка отказа на каждом
запуске).

ПОЧЕМУ ИМЕННО COMMODITIES. Эндпоинт `/data/wow/auctions/commodities` отдаёт
расходники и реагенты сразу по ВСЕМУ региону — травы, руду, кожу, ткань,
флаконы, зелья, пыль для чар, еду. Реалм для них не важен: рынок общий,
данные одинаковы для всех. Это и есть основной предмет спекуляции, и
единственный кусок аукциона, который честно меряется одним запросом.
Обычные лоты (шмот, петы, маунты) живут отдельно, у каждого связанного
реалма свой снимок в десятки мегабайт — осознанно не берём.

ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ ЖЕТОНА. У жетона Blizzard отдаёт одно число, и вся
работа — сохранить его. Здесь приходит СТАКАН: 265 тысяч лотов по 12
тысячам предметов (замерено 2026-08-06). Хранить стакан целиком нельзя —
это сотни мегабайт в месяц. Поэтому каждый предмет сворачивается в четыре
числа, и вот почему именно в эти.

ПОЧЕМУ НЕ «СРЕДНЯЯ ЦЕНА» И НЕ «ОБЩИЙ ОБОРОТ». Первая попытка ранжировать
предметы по `сумма(количество × цена)` дала мусор: в топ вылезла «Почти
свинина» с оборотом 23 миллиарда золота. Причина — на аукционе всегда
висят одиночные лоты по конской цене, и в сумме они перевешивают весь
реальный рынок. Средняя цена больна тем же. Поэтому:

* `floor`    — цена самого дешёвого лота. По ней покупают прямо сейчас.
* `market`   — цена, к которой пол ВЕРНЁТСЯ, если выкупить нижние 15%
               предложения. Это уровень, а не среднее: именно по нему
               получится перепродать, когда дешёвый хвост разобран.
* `deal_qty` — сколько единиц лежит НИЖЕ этого уровня.
* `deal_cost`— во что обойдётся выкупить их все.
* `quantity`, `lots` — глубина рынка. Без них не отличить настоящий товар
               от коллекционной редкости, которой на весь EU 200 штук.

ПОЧЕМУ НЕ ПРОСТО РАЗРЫВ «ПОЛ — РЫНОК» (ошибка, пойманная 2026-08-06 на
первом же прогоне). Сначала «недооценённость» считалась как процент, на
который пол ниже рынка. Метрика выдала «Светоткань: пол 0.25 з, рынок
60.67 з, ниже на 99.6%» и поставила её первой находкой дня. Это мусор:
процент меряет ПЕРЕКОС СТАКАНА, а не выгоду. Если по 0.25 з висит три
штуки, выкупить и перепродать их — заработать 180 золота, то есть ничего.

Считаем поэтому не процент, а ДЕНЬГИ: `deal_qty × market − deal_cost`.
Такой ответ невозможно перепутать с находкой, потому что у мелкого
хвоста он сам по себе мелкий. Комиссию аукциона (5% на товарах) вычитаем
там же, в аналитике, — иначе цифра льстила бы.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import blizzard

# Доля предложения, по которой считаем «рыночную» цену. 15% выбраны так,
# чтобы отсечь одиночные дешёвые выбросы снизу и китов сверху, но не
# уехать в середину стакана, откуда никто не покупает.
MARKET_SHARE = 0.15


@dataclass(frozen=True)
class Quote:
    """Свёртка стакана по одному предмету."""

    item_id: int
    floor: int  # медь за единицу, самый дешёвый лот
    market: int  # медь за единицу, уровень восстановления пола
    quantity: int  # всего единиц выставлено
    lots: int  # число лотов
    deal_qty: int = 0  # единиц дешевле уровня восстановления
    deal_cost: int = 0  # медь, чтобы выкупить их все
    # Сколько единиц уже стоит ПО цене восстановления. Это очередь, в
    # которую встанет перекупщик, когда перевыставит купленное.
    wall_qty: int = 0
    # Сам дешёвый хвост ступеньками: ((цена, количество), ...) по возрастанию.
    #
    # ЗАЧЕМ, ЕСЛИ ЕСТЬ deal_qty И deal_cost. Те два числа описывают ровно
    # одну операцию — «выкупить хвост целиком», — и потому отвечают на
    # вопрос, которого никто не задавал: у человека конечный банк, и на
    # хвост из 40 тысяч единиц золота попросту нет. Со ступеньками
    # аналитика считает, сколько единиц влезает в ЗАДАННЫЙ бюджет и по
    # какой средней цене они возьмутся, — а это уже про настоящую сделку.
    #
    # В CSV не пишется и в git не попадает: живёт ровно столько, сколько
    # живёт снимок в памяти. Приложение запрашивает снимок живьём (см.
    # app._load_auction), поэтому точный расчёт всегда идёт по свежему
    # стакану, а не по часовой давности из истории.
    deal_book: tuple[tuple[int, int], ...] = ()

    @property
    def depth_gold(self) -> float:
        """Сколько золота лежит в стакане по рыночной цене — мера ликвидности."""
        return self.quantity * self.market / blizzard.COPPER_PER_GOLD

    def upside(self, fee: float = 0.05) -> float:
        """Сколько золота даст выкуп дешёвого хвоста, за вычетом комиссии.

        Именно ЭТО и есть «недооценённость». Процентный разрыв пола и
        рынка на такой вопрос не отвечает: у стакана с тремя дешёвыми
        штуками он огромен, а денег там нет.
        """
        gross = self.deal_qty * self.market * (1 - fee) - self.deal_cost
        return gross / blizzard.COPPER_PER_GOLD


# Остаток времени, при котором исчезновение лота НЕЛЬЗЯ объяснить
# истечением срока. Корзины Blizzard: SHORT (<30 мин), MEDIUM (30 мин–2 ч),
# LONG (2–12 ч), VERY_LONG (>12 ч). При часовом шаге снимков SHORT и
# MEDIUM могли протухнуть сами, а LONG и VERY_LONG — нет.
ALIVE_TIME_LEFT = frozenset({"LONG", "VERY_LONG"})


@dataclass(frozen=True)
class Snapshot:
    region: str
    updated: datetime  # время САМОЙ Blizzard, ключ дедупликации
    quotes: dict[int, Quote]
    # {id лота: (предмет, количество, остаток времени, цена)} — только по
    # тем предметам, что просили отслеживать. Нужен для сравнения соседних
    # снимков: что ушло (sold_between) и что пришло (added_between).
    lots: dict[int, tuple[int, int, str, int]] = field(default_factory=dict)


def sold_between(prev: Snapshot, curr: Snapshot) -> dict[int, int]:
    """Сколько единиц ушло с прилавка между двумя снимками, по предметам.

    КАК ЭТО РАБОТАЕТ. У каждого лота есть свой `id`, и он сохраняется
    между снимками — проверено на живых данных 2026-08-07: из 17 631 лота
    id уцелели у 84.3%, и у 99.8% выживших совпали предмет, цена и
    количество. Значит id опознаёт ТОТ ЖЕ САМЫЙ лот, а не переиспользуется.

    Дальше смотрим на пропавшие лоты и на их остаток времени в прошлом
    снимке. Лот с запасом LONG/VERY_LONG за час протухнуть не мог —
    значит его забрали с прилавка.

    ЧЕГО ЭТА МЕТРИКА НЕ УМЕЕТ, И ЭТО ВАЖНО. Отличить покупку от снятия
    лота продавцом невозможно: наружу и то и другое выглядит одинаково —
    лот был и пропал. А продавцы снимают постоянно, чтобы перевыставить
    дешевле и подрезать конкурента. Поэтому число — ВЕРХНЯЯ ОЦЕНКА
    продаж, а не сами продажи. Для сравнения товаров между собой годится
    отлично; обещать по нему «продастся за N часов» — нет.
    """
    if not prev.lots or not curr.lots:
        return {}
    gone: dict[int, int] = {}
    for lot_id, (item_id, quantity, time_left, _price) in prev.lots.items():
        if lot_id in curr.lots:
            continue
        if time_left in ALIVE_TIME_LEFT:
            gone[item_id] = gone.get(item_id, 0) + quantity
    return gone


def added_between(prev: Snapshot, curr: Snapshot) -> dict[int, int]:
    """Сколько ДЕШЁВЫХ единиц выставили заново между снимками, по предметам.

    ЗАЧЕМ ЭТО ВООБЩЕ НУЖНО. Продажи отвечают на вопрос «уходит ли товар», и
    этого мало. Перекупщик, выкупивший дешёвый хвост и перевыставивший его,
    зарабатывает не тогда, когда товар вообще покупают, а тогда, когда его
    покупают БЫСТРЕЕ, чем другие продавцы подвозят новый дешёвый.

    Ровно на это Карен и жаловался: выкупаешь хвост, перевыставляешь, а
    через час снизу встаёт кто-то ещё дешевле, и надо снимать и заново
    подрезать. Если приток дешёвого предложения обгоняет спрос, товар —
    яма независимо от того, какой красивый разрыв показывает снимок.

    Меряем только НИЖНЮЮ часть стакана: лот, выставленный дороже уровня
    восстановления, встаёт позади нас и подрезанием не является. Уровень
    берём из текущего снимка — тот же, относительно которого считается
    выкупаемый хвост.

    ТА ЖЕ ОГОВОРКА, ЧТО У ПРОДАЖ: отличить нового продавца от старого,
    снявшего и перевыставившего лот, снаружи невозможно. Но для нашего
    вопроса это и не нужно — подрезание есть подрезание, кто бы его ни
    делал.
    """
    if not prev.lots or not curr.lots:
        return {}
    fresh: dict[int, int] = {}
    for lot_id, (item_id, quantity, _time_left, price) in curr.lots.items():
        if lot_id in prev.lots:
            continue
        quote = curr.quotes.get(item_id)
        if quote is None or price >= quote.market:
            continue
        fresh[item_id] = fresh.get(item_id, 0) + quantity
    return fresh


# --------------------------------------------------------------------------
# Память между отрезками.
#
# ЗАЧЕМ (найдено на данных 2026-08-09). Измерение продаж требует застать
# ДВА соседних часовых снимка одним процессом. Отрезок сборщика длится 30
# минут, а Blizzard обновляет commodities около 41-й минуты часа, — и
# граница попадала внутрь отрезка, только если тот стартовал в нужные
# полчаса. Итог за двое суток работы: 42 снимка и РОВНО ДВА измерения,
# ни у одного из 409 товаров не набралось нужного минимума. Предохранитель
# «разойдётся за N часов» существовал в коде и молчал в жизни.
#
# Лечение — отдать прошлый снимок следующему процессу через файл. Не в
# git: там это были бы мегабайты мусора каждый час (и такое правило в
# проекте уже записано). Файл живёт во временной папке раннера, переживает
# ровно границу отрезков внутри одного запуска workflow, и потеря его
# ничего не ломает — просто вернётся прежнее поведение.
# --------------------------------------------------------------------------

STATE_HEADER = ("lot_id", "item_id", "quantity", "time_left", "price")


def save_lots(snapshot: Snapshot, path: Path | str) -> None:
    """Запомнить лоты снимка для следующего процесса. Молча терпит отказ."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("updated_utc", snapshot.updated.isoformat(), "", "", ""))
            writer.writerow(STATE_HEADER)
            for lot_id, (item_id, quantity, time_left, price) in snapshot.lots.items():
                writer.writerow((lot_id, item_id, quantity, time_left, price))
        temp.replace(path)
    except OSError as error:
        # Состояние — ускоритель, а не данные. Уронить из-за него сбор
        # нельзя: пропущенное измерение стоит строки в CSV, упавший
        # сборщик стоит часа истории.
        print(f"[KMARKET] Не удалось сохранить лоты: {error}", file=sys.stderr)


def load_lots(region: str, path: Path | str) -> Snapshot | None:
    """Поднять лоты прошлого процесса. None — если их нет или файл битый."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            head = next(reader, None)
            if not head or head[0] != "updated_utc":
                return None
            updated = datetime.fromisoformat(head[1])
            next(reader, None)  # строка заголовка колонок
            lots: dict[int, tuple[int, int, str, int]] = {}
            for row in reader:
                try:
                    lots[int(row[0])] = (int(row[1]), int(row[2]), row[3], int(row[4]))
                except (IndexError, ValueError):
                    continue  # битая строка не повод терять весь файл
    except (OSError, ValueError, StopIteration):
        return None
    if not lots:
        return None
    # quotes пустые намеренно: сравнению нужны ЛОТЫ прошлого снимка и
    # КОТИРОВКИ текущего, а прошлые котировки не нужны никому.
    return Snapshot(region=region, updated=updated, quotes={}, lots=lots)


def _fold(
    book: list[tuple[int, int]]
) -> tuple[int, int, int, int, int, int, int, tuple[tuple[int, int], ...]]:
    """Стакан [(цена, количество), ...] → показатели предмета.

    Возвращает (floor, market, quantity, lots, deal_qty, deal_cost, wall_qty,
    deal_book).

    `market` — это ЦЕНА НА ГРАНИЦЕ нижних MARKET_SHARE предложения, то
    есть уровень, к которому вернётся пол, когда дешёвый хвост разберут.
    Раньше здесь считалось среднее по этому же куску, и оно давало
    бессмысленные «скидки под 100%» на перекошенных стаканах.
    """
    book.sort()
    total = sum(quantity for _, quantity in book)
    floor = book[0][0]

    target = total * MARKET_SHARE
    seen = 0
    market = floor
    for price, quantity in book:
        market = price
        seen += quantity
        if seen >= target:
            break

    # Всё, что дешевле уровня восстановления, и есть выкупаемый хвост.
    # Ступеньки складываем по ходу: аналитике нужен не только итог, но и
    # то, как хвост устроен внутри, — чтобы отрезать от него кусок по
    # размеру кошелька.
    deal_qty = deal_cost = 0
    steps: list[tuple[int, int]] = []
    for price, quantity in book:
        if price >= market:
            break
        deal_qty += quantity
        deal_cost += price * quantity
        # Лоты по одной цене склеиваем: их бывают сотни, а ступенька у
        # них общая, и для расчёта покупки разницы никакой.
        if steps and steps[-1][0] == price:
            steps[-1] = (price, steps[-1][1] + quantity)
        else:
            steps.append((price, quantity))

    # Сколько уже стоит ПО цене восстановления — это очередь, в которую
    # встанешь при перевыставлении. Без неё расчёт навара льстит:
    # выкупить хвост из 25 тысяч перед стеной в 210 тысяч можно, а вот
    # продать — только после того, как стена разойдётся.
    wall_qty = sum(quantity for price, quantity in book if price == market)

    return floor, market, total, len(book), deal_qty, deal_cost, wall_qty, tuple(steps)


def fetch(
    region: str, access_token: str, track_lots: set[int] | None = None
) -> Snapshot:
    """Снимок товарного аукциона региона.

    Ответ большой (2.9 МБ сжатых, 24.8 МБ распакованных), поэтому идёт
    через _http_json_dated с длинным таймаутом: тридцати секунд ему мало.

    `track_lots` — предметы, по которым запомнить отдельные лоты (для
    измерения продаж). Только они: держать в памяти все 265 тысяч лотов
    незачем, а по списку слежки их около семнадцати тысяч.
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
    lots: dict[int, tuple[int, int, str]] = {}
    for lot in data.get("auctions", ()):
        try:
            item_id = int(lot["item"]["id"])
            price = int(lot["unit_price"])
            quantity = int(lot["quantity"])
        except (KeyError, TypeError, ValueError):
            continue  # битый лот не повод терять весь снимок
        if quantity > 0 and price > 0:
            books.setdefault(item_id, []).append((price, quantity))
            if track_lots and item_id in track_lots:
                try:
                    lots[int(lot["id"])] = (
                        item_id,
                        quantity,
                        str(lot.get("time_left") or ""),
                        price,
                    )
                except (KeyError, TypeError, ValueError):
                    pass

    if not books:
        raise RuntimeError(f"Снимок аукциона {region.upper()} пуст — это не норма")

    quotes = {}
    for item_id, book in books.items():
        quotes[item_id] = Quote(item_id, *_fold(book))

    return Snapshot(
        region=region,
        # Заголовка может не быть — тогда датируем моментом опроса. Хуже,
        # чем время Blizzard (дедупликация станет неточной), но лучше, чем
        # уронить сбор из-за отсутствующего заголовка.
        updated=(updated or datetime.now(timezone.utc)).replace(microsecond=0),
        quotes=quotes,
        lots=lots,
    )
