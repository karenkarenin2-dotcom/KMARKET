"""Перцентили и тренд.

Перцентиль — центральная величина всего KMARKET. «Дёшево» и «дорого» —
слова без смысла: цена жетона выросла со 153 тысяч в 2020-м до 370 тысяч
сегодня, и любой абсолютный порог устаревает за месяцы. А «текущая цена
ниже, чем в 88% моментов за последние 90 дней» — утверждение, которое
одинаково верно и в 2020-м, и в 2030-м.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import frame

# Окна, по которым считаем всё. 90 дней — основное для решения (текущий
# режим рынка), 365 — контекст (не сидим ли мы в аномально дорогом году).
WINDOWS: tuple[tuple[str, int], ...] = (
    ("7 дней", 7),
    ("30 дней", 30),
    ("90 дней", 90),
    ("год", 365),
    ("всё время", 100_000),
)

DECISION_WINDOW = 90
CONTEXT_WINDOW = 365


def percentile_of(values: pd.Series, value: float) -> float:
    """Доля точек СТРОГО дешевле `value`, в процентах.

    0 — дешевле не было никогда, 100 — дороже не было никогда.
    """
    if values.empty:
        return float("nan")
    return float((values < value).sum()) / len(values) * 100.0


@dataclass
class WindowStats:
    label: str
    days: int
    points: int
    low: float
    median: float
    high: float
    percentile: float

    @property
    def spread_pct(self) -> float:
        """Размах окна в процентах от минимума — насколько вообще есть за что бороться."""
        return (self.high / self.low - 1) * 100 if self.low else 0.0


def window_stats(series: pd.Series, current: float, label: str, days: int) -> WindowStats | None:
    chunk = frame.window(series, days)
    if len(chunk) < 10:  # на горстке точек статистика врёт больше, чем помогает
        return None
    return WindowStats(
        label=label,
        days=days,
        points=len(chunk),
        low=float(chunk.min()),
        median=float(chunk.median()),
        high=float(chunk.max()),
        percentile=percentile_of(chunk, current),
    )


def all_windows(series: pd.Series, current: float) -> list[WindowStats]:
    stats = (window_stats(series, current, label, days) for label, days in WINDOWS)
    return [s for s in stats if s is not None]


@dataclass
class Trend:
    """Куда и как быстро едет цена. Отвечает на «падение уже кончилось?»."""

    change_24h_pct: float | None = None
    change_7d_pct: float | None = None
    change_30d_pct: float | None = None
    direction: str = "неизвестно"  # растёт / падает / стоит
    notes: list[str] = field(default_factory=list)


def _change_pct(series: pd.Series, current: float, days: float) -> float | None:
    """Изменение относительно цены `days` суток назад (ближайшая точка не новее)."""
    if series.empty:
        return None
    target = series.index[-1] - pd.Timedelta(days=days)
    past = series.loc[:target]
    if past.empty:
        return None
    return (current / float(past.iloc[-1]) - 1) * 100


def trend(series: pd.Series, current: float) -> Trend:
    result = Trend(
        change_24h_pct=_change_pct(series, current, 1),
        change_7d_pct=_change_pct(series, current, 7),
        change_30d_pct=_change_pct(series, current, 30),
    )

    day = result.change_24h_pct
    if day is None:
        return result
    if day > 1.0:
        result.direction = "растёт"
    elif day < -1.0:
        result.direction = "падает"
    else:
        result.direction = "стоит"

    week = result.change_7d_pct
    if week is not None and day is not None:
        # Падение «выдыхается», если недельное падение сильное, а суточное — нет.
        if week < -2 and day > -0.5:
            result.notes.append("недельное падение замедляется — дно может быть близко")
        elif week < -2 and day < -1:
            result.notes.append("падение продолжается — дно скорее всего ещё ниже")
        elif week > 2 and day > 1:
            result.notes.append("рост устойчивый, а не разовый скачок")
    return result
