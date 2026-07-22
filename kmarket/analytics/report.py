"""Сборка полного отчёта — единственное, что нужно знать дашборду.

Возвращает обычные словари и списки: слой представления не должен ничего
знать ни про pandas, ни про то, как считается перцентиль.

Отчёт КЭШИРУЕТСЯ: перебор правил в бэктесте — это десятки тысяч срезов
по истории, несколько секунд работы. Цена жетона обновляется раз в 20
минут, так что считать чаще, чем раз в несколько минут, попросту нечего.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime, timezone

import pandas as pd

from .. import config
from . import backtest, frame, percentile, seasonality, verdict

CACHE_TTL_SECONDS = 240
_cache: dict[str, tuple[float, dict]] = {}


def _local(moment: pd.Timestamp) -> str:
    return moment.tz_convert(config.LOCAL_TZ).isoformat()


def chart_points(region: str, days: int, max_points: int = 900) -> list[list]:
    """Точки для графика в МЕСТНОМ времени, прорежённые до вменяемого числа."""
    chunk = frame.window(frame.load(region), days)
    if chunk.empty:
        return []
    if len(chunk) > max_points:
        chunk = chunk.iloc[:: len(chunk) // max_points + 1]
    local = chunk.tz_convert(config.LOCAL_TZ)
    return [[moment.isoformat(), round(float(price))] for moment, price in local.items()]


def _build(region: str) -> dict:
    series = frame.load(region)
    if series.empty:
        return {"region": region, "empty": True}

    updated = series.index[-1]
    current = float(series.iloc[-1])
    windows = percentile.all_windows(series, current)
    movement = percentile.trend(series, current)
    density = frame.coverage(series, days=30)
    call = verdict.decide(
        current, windows, movement, max_gap_hours=density.get("max_gap_hours")
    )

    season = seasonality.compute(series)
    results = backtest.grid(series)

    # Бэктест ИМЕННО ТОГО правила, по которому вынесен вердикт — иначе
    # цифра экономии на экране относилась бы к другой стратегии.
    rule = {"window_days": percentile.DECISION_WINDOW, "threshold": verdict.BUY_PERCENTILE}
    stock = backtest.stockpile(series, **rule, per_year=12)

    now = datetime.now(timezone.utc)
    return {
        "region": region,
        "empty": False,
        "generated_at": now.isoformat(),
        "current": {
            "price": round(current),
            "updated_utc": updated.isoformat(),
            "updated_local": _local(updated),
            "age_minutes": round((now - updated.to_pydatetime()).total_seconds() / 60),
        },
        "history": {
            "points": int(len(series)),
            "since": series.index[0].isoformat(),
            "coverage": density,
        },
        "verdict": asdict(call),
        "trend": asdict(movement),
        "windows": [asdict(w) | {"spread_pct": round(w.spread_pct, 1)} for w in windows],
        "seasonality": asdict(season) if season else None,
        "backtest": {
            "rule": rule,
            "stockpile": asdict(stock) if stock else None,
            "grid": [asdict(r) for r in results],
        },
        "timezones": {"local": config.LOCAL_TZ, "server": config.SERVER_TZ},
    }


def report(region: str = config.PRIMARY_REGION, *, fresh: bool = False) -> dict:
    cached = _cache.get(region)
    if cached and not fresh and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    built = _build(region)
    _cache[region] = (time.monotonic(), built)
    return built
