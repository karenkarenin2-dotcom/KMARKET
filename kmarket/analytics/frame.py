"""История как pandas.Series — одна точка входа для всей аналитики.

Индекс всегда tz-aware UTC, значения — цена в ЗОЛОТЕ (не в меди): считать
удобнее в тех же единицах, в которых цена показывается человеку.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from .. import config, storage
from ..blizzard import COPPER_PER_GOLD


def load(region: str = config.PRIMARY_REGION) -> pd.Series:
    """Вся история региона: архив + собственные замеры, в золоте."""
    rows = storage.load_history(region)
    if not rows:
        return pd.Series(dtype="float64", index=pd.DatetimeIndex([], tz="UTC"), name=region)
    series = pd.Series(
        [copper / COPPER_PER_GOLD for _, copper in rows],
        index=pd.to_datetime([moment for moment, _ in rows], utc=True),
        name=region,
    )
    return series.sort_index()


def window(series: pd.Series, days: float, *, end: pd.Timestamp | None = None) -> pd.Series:
    """Последние `days` суток истории (по умолчанию — до последней точки)."""
    if series.empty:
        return series
    end = end if end is not None else series.index[-1]
    return series.loc[end - timedelta(days=days) : end]


def coverage(series: pd.Series, days: float = 30) -> dict:
    """Насколько плотные данные в окне — чтобы честно показывать надёжность.

    Архив прорежен неравномерно (свежий месяц — 20 минут, 2021 год — 6.5 часов),
    поэтому интерфейс обязан показывать, на скольких точках стоит вывод,
    а не делать вид, что все цифры одинаково надёжны.
    """
    chunk = window(series, days)
    if len(chunk) < 2:
        return {"points": len(chunk), "median_gap_minutes": None, "days": days}
    gaps = chunk.index.to_series().diff().dropna()
    return {
        "points": int(len(chunk)),
        "median_gap_minutes": round(gaps.dt.total_seconds().median() / 60, 1),
        "max_gap_hours": round(gaps.dt.total_seconds().max() / 3600, 1),
        "days": days,
    }
