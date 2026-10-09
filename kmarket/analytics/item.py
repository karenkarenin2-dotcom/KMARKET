"""Вердикт по товару аукциона: брать, ждать или продавать.

ЧЕМ ОТЛИЧАЕТСЯ ОТ ЖЕТОНА. Жетон Карен только покупает, поэтому там вердикт
односторонний и ищет дно. Товар он может и купить, и продать, — значит
«дорого» здесь не только «не бери», но и «пора продавать запас».

НАПРАВЛЕНИЕ СОБЫТИЯ ТО ЖЕ, ЧТО У ЖЕТОНА, А ВЫВОД ОБРАТНЫЙ. Перед контентом
дорожает и то и другое: жетон скупают за золото, реагенты разбирают под
крафт. Для жетона фаза «до» значит «не бери», для товара — «продавай
запасённое». После события всё оседает, и это фаза набора.

ПОЧЕМУ ВСЕ ОКНА КОРОЧЕ. Внешнего архива по аукциону не существует, история
копится с 2026-08-06. Окно решения — 30 дней (у жетона 90), и вердикт
честно говорит, на скольких днях стоит. Выдуманная точность на деньгах
опаснее честного «данных мало».

ПРОШЛЫЙ ЦИКЛ — ГЛАВНАЯ ПОДСКАЗКА НА БУДУЩЕЕ. У жетона эффект события
измерен на 18 событиях. У товаров истории на один цикл (патч 12.1 и сезон
2, август 2026), и он показателен: «Зачарованный гелиотроп» поднялся с 380
до 525 з к старту сезона и за два месяца осел до 148 з. Одна точка — ещё
не закономерность, поэтому цикл показывается как факт, а не как обещание.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import events
from .percentile import Trend, WindowStats

BUY = "buy"
HOLD = "hold"
SELL = "sell"

DECISION_WINDOW = 30
BUY_PERCENTILE = 25.0
SELL_PERCENTILE = 75.0
DEEP_BOTTOM = 10.0

# События ближе этого числа дней друг к другу — один цикл. Патч 12.1 и
# старт сезона 2 разделяли шесть дней, и рынок отыграл их одной волной.
CLUSTER_DAYS = 14


@dataclass
class Verdict:
    state: str
    title: str
    summary: str
    reasons: list[str] = field(default_factory=list)
    confidence: str = "низкая"


def _gold(value: float) -> str:
    if value >= 100:
        return f"{value:,.0f}".replace(",", " ")
    return f"{value:.1f}".replace(".", ",")


def _date(moment: pd.Timestamp) -> str:
    months = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
    return f"{moment.day} {months[moment.month - 1]}"


def cycle(series: pd.Series) -> dict | None:
    """Как товар прошёл последний цикл контента, который попал в историю.

    Цикл — группа событий ближе CLUSTER_DAYS друг к другу. Нужна хотя бы
    пара дней истории ДО первого из них, иначе «до» мерить не с чем.
    """
    if len(series) < 2:
        return None
    daily = series.resample("1D").median().dropna()
    if len(daily) < 7:
        return None
    start, end = daily.index[0], daily.index[-1]

    inside = [
        e
        for e in events.all_events()
        if e.kind != events.PREPATCH
        and start + pd.Timedelta(days=2) <= pd.Timestamp(e.date, tz="UTC") <= end
    ]
    if not inside:
        return None

    clusters: list[list[events.Event]] = []
    for event in inside:
        moment = pd.Timestamp(event.date, tz="UTC")
        if clusters and (moment - pd.Timestamp(clusters[-1][-1].date, tz="UTC")).days <= CLUSTER_DAYS:
            clusters[-1].append(event)
        else:
            clusters.append([event])
    group = clusters[-1]
    first = pd.Timestamp(group[0].date, tz="UTC")
    last = pd.Timestamp(group[-1].date, tz="UTC")

    before = daily.loc[first - pd.Timedelta(days=7) : first - pd.Timedelta(days=1)]
    if len(before) < 2:
        return None
    around = daily.loc[first - pd.Timedelta(days=7) : last + pd.Timedelta(days=10)]
    peak_at = around.idxmax()
    after_peak = daily.loc[peak_at:]
    low_at = after_peak.idxmin()

    # Подпись — по ручному календарю: у него человеческие названия. Найденное
    # в API («Сезон Мифик+ 18») дублирует те же даты под техническим именем.
    named = [e for e in group if e in events.EVENTS] or group[:1]
    return {
        "label": " и ".join(e.label for e in named),
        "first": group[0].date,
        "last": group[-1].date,
        "before": round(float(before.median()), 2),
        "peak": round(float(around.max()), 2),
        "peak_date": peak_at.date().isoformat(),
        "low": round(float(after_peak.min()), 2),
        "low_date": low_at.date().isoformat(),
        "now": round(float(daily.iloc[-1]), 2),
        "rise_pct": round((float(around.max()) / float(before.median()) - 1) * 100, 1),
        "fall_pct": round((float(daily.iloc[-1]) / float(around.max()) - 1) * 100, 1),
        "_peak_ts": peak_at,
        "_low_ts": low_at,
    }


def _cycle_reason(c: dict) -> str:
    return (
        f"Прошлый цикл ({c['label']}): за неделю до — {_gold(c['before'])} з, "
        f"пик {_gold(c['peak'])} з ({_date(c['_peak_ts'])}, {c['rise_pct']:+.0f}%), "
        f"потом спад до {_gold(c['low'])} з ({_date(c['_low_ts'])}). "
        f"Сейчас на {abs(c['fall_pct']):.0f}% ниже пика."
    )


def _confidence(series: pd.Series) -> str:
    span = (series.index[-1] - series.index[0]).days if len(series) > 1 else 0
    if span < 30 or len(series) < 100:
        return "низкая"
    if span < 120:
        return "средняя"
    return "высокая"


def decide(
    series: pd.Series,
    current: float,
    windows: list[WindowStats],
    trend: Trend,
    *,
    event: dict | None,
    upcoming: list[dict],
) -> tuple[Verdict, dict | None]:
    """Вердикт и разбор прошлого цикла (он же уходит в интерфейс отдельно)."""
    span = (series.index[-1] - series.index[0]).days if len(series) > 1 else 0
    decision = next((w for w in windows if w.days == DECISION_WINDOW), None)
    whole = next((w for w in windows if w.days >= 100_000), None)
    found_cycle = cycle(series)

    if decision is None:
        reasons = [f"Истории {span} дн — для уровня цены нужно хотя бы {DECISION_WINDOW}."]
        if found_cycle:
            reasons.append(_cycle_reason(found_cycle))
        return (
            Verdict(HOLD, "КОПИМ", "Данных пока мало для вывода", reasons, "низкая"),
            found_cycle,
        )

    p = decision.percentile
    reasons = [
        f"Цена дешевле, чем в {100 - p:.0f}% моментов за {DECISION_WINDOW} дней "
        f"(перцентиль {p:.0f}, диапазон {_gold(decision.low)}–{_gold(decision.high)} з)."
    ]
    if p <= BUY_PERCENTILE:
        state = BUY
    elif p >= SELL_PERCENTILE:
        state = SELL
    else:
        state = HOLD

    if whole is not None and whole.days != decision.days:
        reasons.append(
            f"За всю историю ({span} дн): {whole.percentile:.0f}-й перцентиль, "
            f"от {_gold(whole.low)} до {_gold(whole.high)} з."
        )
        if state == BUY and whole.percentile > 60:
            state = HOLD
            reasons.append(
                "Дёшево только на фоне последнего месяца — по меркам всей истории "
                "цена ещё высокая."
            )

    if state == BUY and trend.direction == "падает" and p > DEEP_BOTTOM:
        state = HOLD
        reasons.append("Цена падает прямо сейчас — есть смысл дать падению закончиться.")
        # Заметка тренда говорит то же самое другими словами — не дублируем.
        reasons.extend(n for n in trend.notes if not n.startswith("падение продолжается"))
    else:
        reasons.extend(trend.notes)

    # Фаза события сдвигает уже имеющееся мнение. Собственного мнения у
    # неё нет: фаза одна на весь рынок и про конкретный товар не знает.
    if event:
        days, phase = event["days"], event["phase"]
        if phase == "before":
            reasons.append(
                f"Через {days} дн — {event['label']}. Перед контентом реагенты и "
                f"расходники разбирают под крафт: цена обычно растёт. Это фаза "
                f"продажи запасённого, а не набора."
            )
            if state == BUY and p > DEEP_BOTTOM:
                state = HOLD
            elif state == HOLD:
                state = SELL
        elif phase == "just_happened":
            reasons.append(
                f"{event['label']} — {abs(days)} дн назад. Пик спроса обычно здесь; "
                f"дальше цена оседает неделями."
            )
            if state == HOLD and p >= 50:
                state = SELL
        else:
            reasons.append(
                f"{event['label']} прошло {abs(days)} дн назад — ажиотаж спал, "
                f"это фаза набора к следующему циклу."
            )
            if state == HOLD and p <= 50:
                state = BUY

    if found_cycle:
        reasons.append(_cycle_reason(found_cycle))
        reasons.append(
            "Один цикл — ещё не закономерность. Но направление то же, что у жетона "
            "на 18 событиях: перед контентом дорого, после — дёшево."
        )

    if not upcoming:
        reasons.append(
            "Будущих дат в календаре нет — фазу «до» не видно. Когда Blizzard "
            "объявит следующий патч или сезон, дату нужно внести."
        )

    titles = {
        BUY: ("БРАТЬ", "Дёшево по меркам своей истории"),
        HOLD: ("ЖДАТЬ", "Середина: ни купить, ни продать особенно выгодно"),
        SELL: ("ПРОДАВАТЬ", "Дорого: время продавать запас, а не покупать"),
    }
    title, summary = titles[state]
    return Verdict(state, title, summary, reasons, _confidence(series)), found_cycle
