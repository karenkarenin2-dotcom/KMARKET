"""Аналитика товарного аукциона: что недооценено и что брать под патч.

Нужен pandas — модуль живёт на стороне приложения, не сборщика.

ЧЕМ ЭТА ЗАДАЧА ОТЛИЧАЕТСЯ ОТ ЖЕТОНА. У жетона профиль покупателя
односторонний: Карен покупает за золото и никогда не продаёт, поэтому
«хорошо» = «дёшево», и весь вердикт ищет дно. Здесь профиль
ДВУСТОРОННИЙ — купить дёшево и продать дорого, — значит нужен и сигнал
на продажу, а «дорого» перестаёт быть плохой новостью.

ДВА НЕЗАВИСИМЫХ СИГНАЛА, И ЭТО ВАЖНО.

1. ВЫГОДА ОТ ВЫКУПА ДЕШЁВОГО ХВОСТА работает с первого же снимка и
   истории не требует. Считается в ЗОЛОТЕ, а не в процентах: сколько
   единиц лежит ниже уровня восстановления пола, во что обойдётся их
   выкупить и сколько останется после комиссии аукциона.

   ПРОЦЕНТ ЗДЕСЬ НЕ ГОДИТСЯ, и это ошибка, пойманная на первом прогоне.
   Метрика «на сколько процентов пол ниже рынка» выдала «Светоткань:
   пол 0.25 з, рынок 60.67 з, ниже на 99.6%» первой находкой дня. Но
   процент меряет перекос стакана, а не деньги: под этим полом лежало
   несколько штук, и вся операция стоила бы двух сотен золота. В золоте
   такую находку невозможно перепутать с настоящей.

2. ПЕРЦЕНТИЛЬ ЦЕНЫ требует накопленной истории и заработает не сразу.
   Внешнего архива по commodities не существует (жетону когда-то залили
   бутстрап с 2020 года, здесь заливать неоткуда), поэтому копим с нуля.
   Пока точек мало, модуль обязан ГОВОРИТЬ об этом, а не рисовать
   уверенный перцентиль по трём замерам: выдуманная точность на деньгах
   опаснее честного «данных мало».

БАЗА СРАВНЕНИЯ ТОЛЬКО ЛОКАЛЬНАЯ. Урок, оплаченный на жетоне (см.
docs/НАХОДКИ.md): в трендовом ряду любая метрика с глобальной базой
льстит. Первая версия бэктеста сравнивала цену покупки со средней за все
пять лет и показывала «экономию 12.4%», которая на деле означала «купил
в 2021-м». Здесь тренд ещё резче — патч меняет цены реагентов в разы, —
поэтому сравниваем предмет только с ЕГО ЖЕ недавним прошлым.

СОБЫТИЕ ГЛАВНЕЕ ПЕРЦЕНТИЛЯ, как и у жетона, но направление ОБРАТНОЕ.
Перед патчем жетон дорожает, потому что его скупают за золото; реагенты
дорожают, потому что все готовятся крафтить. Разница в том, что для
жетона Карен покупатель (и «дорого» = плохо), а реагенты он может
продать в этот самый пик. Поэтому фаза `before` для жетона значит
«не бери», а для аукциона — «пора продавать запасённое».
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd

from .. import config, items, storage, watchlist
from ..blizzard import COPPER_PER_GOLD
from . import events

# Окно, в котором считаем перцентиль цены предмета. Короче, чем у жетона
# (90 дней): реагенты живут циклом патча, и цена трёхмесячной давности
# относится к другому рынку.
WINDOW_DAYS = 21

# Сколько точек считаем минимумом, чтобы вообще произносить слово
# «перцентиль». Ниже — говорим «истории мало» и молчим про уровень.
MIN_POINTS = 24

# Комиссия аукциона на товарах — 5%. Вычитаем всегда: цифра выгоды без
# неё систематически льстит, а решения принимаются по ней.
AH_FEE = 0.05

# Ниже этого выигрыша находка не находка. Порог в ЗОЛОТЕ, а не в
# процентах, — см. шапку модуля про «Светоткань, ниже на 99.6%».
MIN_UPSIDE_GOLD = 500.0

BUY = "buy"
HOLD = "hold"
SELL = "sell"
# Отдельное состояние «сказать нечего». Появилось после первого прогона:
# без него все 80 товаров разом получали совет «продавать», потому что
# истории нет, состояние по умолчанию «держать», а фаза перед патчем его
# переворачивает. Совет продавать 80 товаров сразу — это не анализ
# товара, а фаза патча, размазанная по списку. Молчание честнее.
UNKNOWN = "unknown"

# Минимальная доходность выкупа, ниже которой находка не находка.
# Без неё порядок обманывал: «Огненная вода» с отдачей 41 тыс. стояла
# выше «Затуманенного кристалла» с отдачей 25 тыс., хотя под первую надо
# вложить 2.2 миллиона (1.9%), а под второй — 38 тысяч (67%).
MIN_ROI_PCT = 5.0


@dataclass
class ItemView:
    """Всё, что приложение показывает про один предмет."""

    item_id: int
    name: str
    icon: str = ""
    subclass: str = ""
    quality: str = ""
    rank: int | None = None
    rank_of: int | None = None

    floor_gold: float = 0.0
    market_gold: float = 0.0
    quantity: int = 0
    lots: int = 0

    discount_pct: float = 0.0  # насколько пол ниже уровня восстановления
    deal_qty: int = 0  # единиц дешевле этого уровня
    deal_cost_gold: float = 0.0  # сколько золота нужно вложить
    upside_gold: float = 0.0  # чистая выгода выкупа хвоста, ЗОЛОТО
    roi_pct: float = 0.0  # она же в процентах от вложения
    percentile: float | None = None  # положение цены в своей истории
    points: int = 0  # сколько замеров стоит за перцентилем
    change_pct: float | None = None  # изменение рынка за окно
    supply_change_pct: float | None = None  # изменение предложения за окно

    state: str = HOLD
    reasons: list[str] = field(default_factory=list)


def _gold(copper: float) -> float:
    """Медь в золото. Округляем до сотых: реагенты часто стоят меньше золотого."""
    return round(copper / COPPER_PER_GOLD, 2)


def frame(region: str = config.PRIMARY_REGION) -> pd.DataFrame:
    """История аукциона региона как таблица с индексом по времени."""
    rows = storage.load_auction(region)
    if not rows:
        return pd.DataFrame(
            columns=[
            "updated",
            "item_id",
            "floor",
            "market",
            "quantity",
            "lots",
            "deal_qty",
            "deal_cost",
        ]
        ).set_index("updated")
    table = pd.DataFrame(
        rows, columns=[
            "updated",
            "item_id",
            "floor",
            "market",
            "quantity",
            "lots",
            "deal_qty",
            "deal_cost",
        ]
    )
    table["updated"] = pd.to_datetime(table["updated"], utc=True)
    return table.set_index("updated").sort_index()


def _percentile(series: pd.Series, current: float) -> float:
    """Доля моментов, когда было ДЕШЕВЛЕ текущего. 0 — исторический минимум."""
    if series.empty:
        return 50.0
    return round(float((series < current).mean()) * 100, 1)


def _describe(view: ItemView, event: dict | None) -> None:
    """Проставить состояние и причины. Причины обязательны — см. verdict.py."""
    reasons: list[str] = []

    if view.upside_gold >= MIN_UPSIDE_GOLD and view.roi_pct >= MIN_ROI_PCT:
        qty = f"{view.deal_qty:,}".replace(",", " ")
        cost = f"{view.deal_cost_gold:,.0f}".replace(",", " ")
        gain = f"{view.upside_gold:,.0f}".replace(",", " ")
        reasons.append(
            f"Ниже уровня восстановления лежит {qty} шт: вложить {cost} з, "
            f"вернуть примерно {gain} з чистыми после комиссии."
        )
        # Оговорка обязательна. Метрика меряет РАЗРЫВ в стакане, а не
        # спрос: выкупить дешёвый хвост можно всегда, а вот продастся ли
        # он — вопрос скорости ухода товара, и до накопления истории
        # ответа на него нет. Молчать об этом — значит выдавать
        # арифметику за гарантию прибыли.
        if view.points < MIN_POINTS:
            reasons.append(
                "Скорость продаж по этому товару мы ещё не мерили — выгода тут "
                "арифметическая, а не обещанная. На залежалом товаре хвост можно "
                "выкупить и остаться с ним."
            )

    if view.percentile is None:
        reasons.append(
            f"Истории всего {view.points} замеров — про уровень цены сказать нечего. "
            f"Копим, перцентиль появится сам."
        )
    else:
        reasons.append(
            f"Цена в нижних {view.percentile:.0f}% своих значений за {WINDOW_DAYS} дн."
        )

    if view.supply_change_pct is not None and abs(view.supply_change_pct) >= 25:
        direction = "выросло" if view.supply_change_pct > 0 else "упало"
        reasons.append(
            f"Предложение {direction} на {abs(view.supply_change_pct):.0f}% за окно — "
            + ("рынок затоваривается." if view.supply_change_pct > 0 else "товар выметают.")
        )

    # Фаза события. Для реагентов знак обратный жетону: перед контентом
    # они дорожают, потому что все готовятся крафтить.
    #
    # БЕЗ ИСТОРИИ НАПРАВЛЕНИЕ НЕ НАЗЫВАЕМ. Фаза патча одна на весь рынок,
    # и если разрешить ей самой рождать совет, все 80 товаров разом
    # получат «продавать» — совет, который не про товар, а про календарь.
    # Поэтому фаза только СДВИГАЕТ уже имеющееся мнение, а собственное
    # мнение появляется лишь вместе с перцентилем.
    state = UNKNOWN
    if view.percentile is not None:
        if view.percentile <= 25:
            state = BUY
        elif view.percentile >= 75:
            state = SELL
        else:
            state = HOLD

    if event:
        days, phase = event["days"], event["phase"]
        if phase == "before":
            reasons.append(
                f"Через {days} дн — {event['label']}. Перед контентом реагенты и "
                f"расходники разбирают под крафт: цены обычно идут вверх, это "
                f"фаза продажи запасённого, а не набора."
            )
            if state == BUY and view.percentile is not None and view.percentile > 10:
                # Дёшево перед патчем — это ещё не «дёшево», а «пока не
                # начали разбирать». Брать можно, но без энтузиазма.
                state = HOLD
            elif state == HOLD:
                state = SELL
        elif phase == "after":
            reasons.append(
                f"{event['label']} прошло {abs(days)} дн назад — ажиотаж спал, "
                f"реагенты обычно дешевеют. Фаза набора."
            )
            if state == HOLD:
                state = BUY

    # Выкуп дешёвого хвоста перебивает всё: это арбитраж здесь и сейчас,
    # он не зависит ни от перцентиля, ни от фазы патча.
    if view.upside_gold >= MIN_UPSIDE_GOLD and view.roi_pct >= MIN_ROI_PCT:
        state = BUY

    view.state = state
    view.reasons = reasons


def board(
    region: str = config.PRIMARY_REGION,
    *,
    live: dict | None = None,
    now: pd.Timestamp | None = None,
) -> list[ItemView]:
    """Все предметы под слежкой с показателями и советом.

    `live` — свежий снимок (dict item_id -> Quote) из auction.fetch. Если
    его нет, берём последнюю точку истории.
    """
    table = frame(region)
    event = events.context(now=now)
    watched = watchlist.item_ids()

    cutoff = (now or pd.Timestamp.now(tz="UTC")) - pd.Timedelta(days=WINDOW_DAYS)
    recent = table[table.index >= cutoff] if not table.empty else table

    views: list[ItemView] = []
    for item_id in watched:
        entry = items.get(item_id) or {}
        view = ItemView(
            item_id=item_id,
            name=entry.get("name") or f"Предмет {item_id}",
            icon=entry.get("icon", ""),
            subclass=entry.get("subclass", ""),
            quality=entry.get("quality", ""),
            rank=entry.get("rank"),
            rank_of=entry.get("rank_of"),
        )

        history = recent[recent["item_id"] == item_id] if not recent.empty else recent

        quote = (live or {}).get(item_id)
        if quote is not None:
            floor, market = quote.floor, quote.market
            quantity, lots = quote.quantity, quote.lots
            deal_qty, deal_cost = quote.deal_qty, quote.deal_cost
        elif not history.empty:
            last = history.iloc[-1]
            floor, market = int(last["floor"]), int(last["market"])
            quantity, lots = int(last["quantity"]), int(last["lots"])
            deal_qty, deal_cost = int(last["deal_qty"]), int(last["deal_cost"])
        else:
            continue  # ни живого снимка, ни истории — показывать нечего

        view.floor_gold = _gold(floor)
        view.market_gold = _gold(market)
        view.quantity, view.lots = quantity, lots
        view.discount_pct = round((1 - floor / market) * 100, 1) if market else 0.0
        view.deal_qty = deal_qty
        view.deal_cost_gold = round(deal_cost / COPPER_PER_GOLD, 0)
        view.upside_gold = round(
            (deal_qty * market * (1 - AH_FEE) - deal_cost) / COPPER_PER_GOLD, 0
        )
        view.roi_pct = (
            round(view.upside_gold / view.deal_cost_gold * 100, 1)
            if view.deal_cost_gold
            else 0.0
        )
        view.points = int(len(history))

        if view.points >= MIN_POINTS:
            view.percentile = _percentile(history["market"], market)
            first = float(history["market"].iloc[0])
            if first:
                view.change_pct = round((market / first - 1) * 100, 1)
            first_qty = float(history["quantity"].iloc[0])
            if first_qty:
                view.supply_change_pct = round((quantity / first_qty - 1) * 100, 1)

        _describe(view, event)
        views.append(view)

    return views


def bargains(views: list[ItemView], limit: int = 12) -> list[ItemView]:
    """Недооценённые прямо сейчас — по ЗОЛОТУ, а не по проценту.

    Порядок именно по выгоде: находка, которая даёт двести золота, не
    находка, каким бы красивым ни был её процент (см. шапку модуля).
    """
    worth = [
        v
        for v in views
        if v.upside_gold >= MIN_UPSIDE_GOLD
        and v.roi_pct >= MIN_ROI_PCT
        and v.lots >= 20
    ]
    worth.sort(key=lambda v: v.upside_gold, reverse=True)
    return worth[:limit]


def summary(region: str = config.PRIMARY_REGION, *, live: dict | None = None) -> dict:
    """Готовый к показу срез аукциона."""
    views = board(region, live=live)
    event = events.context()
    stored = watchlist.load()
    return {
        "region": region,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "watchlist_generated": stored.get("generated_utc"),
        "window_days": WINDOW_DAYS,
        "tracked": len(views),
        "event": event,
        "items": [asdict(v) for v in views],
        "bargains": [asdict(v) for v in bargains(views)],
    }
