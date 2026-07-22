"""Сезонность: в какие часы и дни недели жетон исторически дешевле.

ГЛАВНАЯ ЛОВУШКА, которую здесь надо обойти. Считать средние цены по часам
напрямую НЕЛЬЗЯ: за пять лет цена выросла вдвое, и такая «сезонность»
покажет не суточный ритм рынка, а то, в какие часы источник чаще писал
данные в дорогие годы. Поэтому каждая точка сначала переводится в
ОТКЛОНЕНИЕ от своего локального уровня — скользящей медианы за ±3.5 дня.
Долгосрочный тренд уходит, остаётся чистый внутринедельный рисунок.

Считаем по ВРЕМЕНИ EU-СЕРВЕРОВ (Europe/Paris), а не по местному: суточный
ритм создают игроки, ресеты и прайм-тайм, и живут они в серверном времени.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .. import config
from . import frame

WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

# Окно нормализации: неделя. Меньше — начнёт съедать сам суточный сигнал,
# больше — не успеет за реальными сдвигами рынка.
BASELINE_WINDOW = "7D"


@dataclass
class Seasonality:
    """Матрица 7×24 средних отклонений в процентах и что из неё следует."""

    matrix: list[list[float | None]]  # [день недели][час] -> отклонение, %
    counts: list[list[int]]  # сколько точек легло в клетку
    best: list[dict]  # самые дешёвые клетки
    worst: list[dict]  # самые дорогие
    by_weekday: dict[str, float]  # средние по дням недели
    by_hour: dict[int, float]  # средние по часам
    points: int
    days: int
    timezone: str
    reliable: bool


def deviations(series: pd.Series, days: int = 365) -> pd.Series:
    """Отклонение каждой точки от локального уровня, в процентах."""
    chunk = frame.window(series, days)
    if len(chunk) < 50:
        return pd.Series(dtype="float64")
    baseline = chunk.rolling(BASELINE_WINDOW, center=True, min_periods=8).median()
    result = (chunk / baseline - 1) * 100
    return result.dropna()


def compute(series: pd.Series, days: int = 365) -> Seasonality | None:
    values = deviations(series, days)
    if values.empty:
        return None

    local = values.tz_convert(config.SERVER_TZ)
    table = pd.DataFrame(
        {
            "deviation": local.values,
            "weekday": local.index.weekday,
            "hour": local.index.hour,
        }
    )

    grouped = table.groupby(["weekday", "hour"])["deviation"]
    means = grouped.mean()
    sizes = grouped.size()

    matrix: list[list[float | None]] = []
    counts: list[list[int]] = []
    cells: list[dict] = []
    for weekday in range(7):
        row: list[float | None] = []
        count_row: list[int] = []
        for hour in range(24):
            key = (weekday, hour)
            count = int(sizes.get(key, 0))
            count_row.append(count)
            # Клетка на двух-трёх точках — это шум, а не сезонность.
            if count < 4:
                row.append(None)
                continue
            value = round(float(means[key]), 3)
            row.append(value)
            cells.append(
                {"weekday": WEEKDAYS[weekday], "hour": hour, "deviation": value, "count": count}
            )
        matrix.append(row)
        counts.append(count_row)

    if not cells:
        return None

    ordered = sorted(cells, key=lambda c: c["deviation"])
    return Seasonality(
        matrix=matrix,
        counts=counts,
        best=ordered[:5],
        worst=ordered[-5:][::-1],
        by_weekday={
            WEEKDAYS[day]: round(float(value), 3)
            for day, value in table.groupby("weekday")["deviation"].mean().items()
        },
        by_hour={
            int(hour): round(float(value), 3)
            for hour, value in table.groupby("hour")["deviation"].mean().items()
        },
        points=int(len(values)),
        days=days,
        timezone=config.SERVER_TZ,
        # Меньше ~2000 точек на 168 клеток — в среднем меньше дюжины на клетку,
        # это уже гадание. Честно сообщаем интерфейсу.
        reliable=len(values) >= 2000,
    )
