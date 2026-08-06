"""Игровые события и их влияние на цену жетона.

Наблюдение Карена: патчи, дополнения и старты сезонов сильно двигают цену.
Здесь оно не принимается на веру, а измеряется — событийным анализом
(event study) по всей накопленной истории.

МЕТОД. Вокруг каждого события берётся окно ±30 дней, и цены внутри него
переводятся в отклонение от МЕДИАНЫ ЭТОГО ОКНА. Это то же лекарство, что
и в seasonality: без нормализации «эффект патча» выродился бы в «цена
за пять лет выросла», потому что поздние патчи просто пришлись на дорогие
годы. После нормализации кривые разных лет сравнимы и их можно усреднять.

ИСТОЧНИК ДАТ. Кураторский список крупных патчей из проекта wowtoken.app
(github.com/sneaky-emily/wowtoken.app, файл src/patches.js) — только
значимые вехи, без мелких правок. Даты Midnight сверены с анонсом Blizzard
(пре-патч 2026-01-20, запуск 2026-03-02, Season 1 2026-03-17).

ВАЖНО ПРИ ОБНОВЛЕНИИ: список надо пополнять руками по мере выхода патчей.
Blizzard не отдаёт даты патчей через API — автоматизировать нечем.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

PREPATCH = "prepatch"
LAUNCH = "launch"
SEASON = "season"
CONTENT = "content"

KIND_TITLES = {
    PREPATCH: "Пре-патч",
    LAUNCH: "Запуск дополнения",
    SEASON: "Старт сезона",
    CONTENT: "Крупный патч",
}


@dataclass(frozen=True)
class Event:
    label: str
    date: str  # ISO, UTC
    kind: str


EVENTS: tuple[Event, ...] = (
    # Shadowlands
    Event("Shadowlands: пре-патч", "2020-10-13", PREPATCH),
    Event("Shadowlands: запуск", "2020-11-23", LAUNCH),
    Event("Shadowlands: сезон 1", "2020-12-08", SEASON),
    Event("Shadowlands: сезон 2", "2021-07-06", SEASON),
    Event("Shadowlands: сезон 3", "2022-03-01", SEASON),
    Event("Shadowlands: сезон 4", "2022-08-02", SEASON),
    # Dragonflight
    Event("Dragonflight: пре-патч", "2022-10-25", PREPATCH),
    Event("Dragonflight: запуск", "2022-11-28", LAUNCH),
    Event("Dragonflight: сезон 1", "2022-12-12", SEASON),
    Event("Dragonflight: сезон 2", "2023-05-09", SEASON),
    Event("Dragonflight: сезон 3", "2023-11-14", SEASON),
    Event("Dragonflight: сезон 4", "2024-04-23", SEASON),
    # The War Within
    Event("The War Within: пре-патч", "2024-07-23", PREPATCH),
    Event("The War Within: запуск", "2024-08-26", LAUNCH),
    Event("The War Within: сезон 1", "2024-09-11", SEASON),
    Event("The War Within: сезон 2", "2025-03-04", SEASON),
    Event("The War Within: сезон 3", "2025-08-12", SEASON),
    # Midnight
    Event("Midnight: пре-патч", "2026-01-20", PREPATCH),
    Event("Midnight: запуск", "2026-03-02", LAUNCH),
    Event("Midnight: сезон 1", "2026-03-17", SEASON),
    # Патч 12.1 «Curse of Ula'tek». Blizzard объявила 11 августа для NA;
    # в EU контент открывается на сутки позже, а нас интересует ИМЕННО EU —
    # это основной регион, и цена жетона там живёт по своему расписанию.
    Event("Midnight: патч 12.1", "2026-08-12", CONTENT),
    Event("Midnight: сезон 2", "2026-08-18", SEASON),
)

BEFORE_DAYS = 30
AFTER_DAYS = 30


@dataclass
class EventEffect:
    """Усреднённая кривая «что с ценой вокруг события такого типа»."""

    kind: str
    title: str
    events: int
    # [[смещение в днях, среднее отклонение %], ...] от -30 до +30
    curve: list[list[float]] = field(default_factory=list)
    before_pct: float | None = None  # средний уровень за 30..8 дней ДО
    around_pct: float | None = None  # окно ±7 дней
    after_pct: float | None = None  # 8..30 дней ПОСЛЕ
    peak_offset: int | None = None  # день максимума относительно события


def upcoming(limit: int = 3, now: pd.Timestamp | None = None) -> list[dict]:
    """Ближайшие будущие события — чтобы дашборд мог предупредить заранее."""
    now = now or pd.Timestamp.now(tz="UTC")
    ahead = []
    for event in EVENTS:
        moment = pd.Timestamp(event.date, tz="UTC")
        if moment > now:
            ahead.append(
                {
                    "label": event.label,
                    "date": event.date,
                    "kind": event.kind,
                    "in_days": (moment - now).days,
                }
            )
    return sorted(ahead, key=lambda e: e["in_days"])[:limit]


def in_range(start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    """События внутри диапазона — для отметок на графике."""
    result = []
    for event in EVENTS:
        moment = pd.Timestamp(event.date, tz="UTC")
        if start <= moment <= end:
            result.append({"label": event.label, "date": event.date, "kind": event.kind})
    return result


def context(now: pd.Timestamp | None = None, horizon: int = 30) -> dict | None:
    """Ближайшее событие и фаза относительно него — сигнал для вердикта.

    Измерено на 18 событиях (см. study): перед событием цена ЗАДРАНА
    (запуск дополнения: +12% за 30..8 дней до), после — ОБВАЛИВАЕТСЯ
    (−11.8% за 8..30 дней после). Значит фаза важнее любого перцентиля:
    «дёшево» за неделю до запуска и «дёшево» через месяц после — это
    совершенно разные «дёшево».

    ВЫБОР СОБЫТИЯ ИДЁТ ПО ОСТОРОЖНОСТИ, А НЕ ПО БЛИЗОСТИ. Раньше здесь
    побеждало ближайшее по модулю, и на плотном календаре это давало прямо
    неверный совет: 12.1 выходит 12 августа, сезон 2 — 18-го. Четырнадцатого
    числа патч позади на два дня, сезон впереди на четыре, ближайшим по
    модулю оказывается патч — и фаза объявлялась «уже случилось, цена
    начинает оседать», хотя на деле рынок ещё разогревается перед сезоном.
    Пока впереди есть событие внутри горизонта, оно и главное; прошедшие
    смотрим, только когда впереди пусто.
    """
    now = now or pd.Timestamp.now(tz="UTC")
    found: list[dict] = []
    for event in EVENTS:
        moment = pd.Timestamp(event.date, tz="UTC")
        days = (moment.normalize() - now.normalize()).days  # >0 впереди, <0 позади
        if abs(days) > horizon:
            continue
        if days > 0:
            phase = "before"  # цена обычно повышенная и растёт
        elif days >= -7:
            phase = "just_happened"  # перелом
        else:
            phase = "after"  # цена обычно оседает
        found.append(
            {
                "label": event.label,
                "date": event.date,
                "kind": event.kind,
                "days": days,
                "phase": phase,
            }
        )
    if not found:
        return None
    # Сначала по осторожности фазы, внутри фазы — ближайшее.
    order = {"before": 0, "just_happened": 1, "after": 2}
    return min(found, key=lambda e: (order[e["phase"]], abs(e["days"])))


def _curves(daily: pd.Series, kind: str | None) -> list[dict[int, float]]:
    curves: list[dict[int, float]] = []
    for event in EVENTS:
        if kind and event.kind != kind:
            continue
        moment = pd.Timestamp(event.date, tz="UTC")
        chunk = daily.loc[
            moment - pd.Timedelta(days=BEFORE_DAYS) : moment + pd.Timedelta(days=AFTER_DAYS)
        ]
        # Требуем существенное покрытие окна, иначе кривая будет огрызком.
        if len(chunk) < (BEFORE_DAYS + AFTER_DAYS) * 0.6:
            continue
        baseline = float(chunk.median())
        if not baseline:
            continue
        curves.append(
            {
                (day.normalize() - moment.normalize()).days: (float(price) / baseline - 1) * 100
                for day, price in chunk.items()
            }
        )
    return curves


def study(series: pd.Series, kind: str | None = None) -> EventEffect | None:
    """Событийный анализ: усреднённое поведение цены вокруг событий."""
    if series.empty:
        return None
    daily = series.resample("1D").median().dropna()
    curves = _curves(daily, kind)
    if not curves:
        return None

    points: list[list[float]] = []
    for offset in range(-BEFORE_DAYS, AFTER_DAYS + 1):
        values = [c[offset] for c in curves if offset in c]
        if len(values) < max(2, len(curves) // 2):
            continue
        points.append([offset, round(sum(values) / len(values), 2)])

    def band(low: int, high: int) -> float | None:
        chosen = [v for o, v in points if low <= o <= high]
        return round(sum(chosen) / len(chosen), 2) if chosen else None

    peak = max(points, key=lambda p: p[1]) if points else None
    return EventEffect(
        kind=kind or "all",
        title=KIND_TITLES.get(kind, "Все крупные события"),
        events=len(curves),
        curve=points,
        before_pct=band(-30, -8),
        around_pct=band(-7, 7),
        after_pct=band(8, 30),
        peak_offset=int(peak[0]) if peak else None,
    )


def all_studies(series: pd.Series) -> list[EventEffect]:
    studies = [study(series, kind) for kind in (None, PREPATCH, LAUNCH, SEASON)]
    return [s for s in studies if s]
