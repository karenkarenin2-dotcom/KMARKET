"""Сборка полного отчёта — единственное, что нужно знать окну.

Отчёт двух видов: по жетону (`report`) и по товару аукциона
(`item_report`). Устройство одно — история как ряд цен, перцентили, тренд,
недельный ритм, фаза события, — различаются вердикт и длина окон.

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
from ..blizzard import COPPER_PER_GOLD
from . import backtest, events, frame, item, percentile, seasonality, verdict

CACHE_TTL_SECONDS = 240
_cache: dict[str, tuple[float, dict]] = {}


def _local(moment: pd.Timestamp) -> str:
    return moment.tz_convert(config.LOCAL_TZ).isoformat()


# За сколько дней график перестаёт быть интрадей-графиком. Дальше этого
# горизонта показываем дневную медиану: жетон колеблется внутри суток на
# несколько процентов, и на 90 днях сырые точки превращаются в сплошную
# гребёнку, по которой не прочесть ни уровня, ни тренда. Проверено
# глазами на первом же снимке экрана.
DAILY_FROM_DAYS = 45


def chart_data(region: str, days: int, max_points: int = 900) -> dict:
    """Точки графика жетона в МЕСТНОМ времени плюс попавшие в диапазон события."""
    return series_chart(frame.load(region), days, max_points)


def series_chart(series: pd.Series, days: int, max_points: int = 900) -> dict:
    """Точки графика любого ряда цен — жетона или товара."""
    chunk = frame.window(series, days)
    if chunk.empty:
        return {"points": [], "events": []}
    marks = events.in_range(chunk.index[0], chunk.index[-1])
    if days >= DAILY_FROM_DAYS:
        chunk = chunk.resample("1D").median().dropna()
    if len(chunk) > max_points:
        chunk = chunk.iloc[:: len(chunk) // max_points + 1]
    local = chunk.tz_convert(config.LOCAL_TZ)
    return {
        "points": [[moment.isoformat(), round(float(price))] for moment, price in local.items()],
        "events": marks,
    }


def _build(region: str) -> dict:
    series = frame.load(region)
    if series.empty:
        return {"region": region, "empty": True}

    updated = series.index[-1]
    current = float(series.iloc[-1])
    windows = percentile.all_windows(series, current)
    movement = percentile.trend(series, current)
    density = frame.coverage(series, days=30)
    event_now = events.context()
    call = verdict.decide(
        current,
        windows,
        movement,
        max_gap_hours=density.get("max_gap_hours"),
        event=event_now,
    )

    season = seasonality.compute(series)
    results = backtest.grid(series)

    # Бэктест ИМЕННО ТОГО правила, по которому вынесен вердикт — иначе
    # цифра экономии на экране относилась бы к другой стратегии.
    rule = {"window_days": percentile.DECISION_WINDOW, "threshold": verdict.BUY_PERCENTILE}
    stock = backtest.stockpile(series, **rule, per_year=12)

    now = datetime.now(timezone.utc)
    return {
        "kind": "token",
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
        "rhythm": _rhythm(season),
        "events": {
            "now": event_now,
            "upcoming": events.upcoming(3),
            "studies": [asdict(s) for s in events.all_studies(series)],
        },
        "backtest": {
            "rule": rule,
            "stockpile": asdict(stock) if stock else None,
            "grid": [asdict(r) for r in results],
        },
        "timezones": {"local": config.LOCAL_TZ, "server": config.SERVER_TZ},
    }


def _rhythm(season) -> dict | None:
    """Недельный ритм в виде, который окно показывает без пересчёта."""
    if season is None:
        return None
    return {
        "cheapest": season.best[:3],
        "dearest": season.worst[:3],
        "by_weekday": season.by_weekday,
        "spread_pct": round(season.worst[0]["deviation"] - season.best[0]["deviation"], 1)
        if season.best and season.worst
        else None,
        "points": season.points,
        "reliable": season.reliable,
        "timezone": season.timezone,
    }


def item_report(rows: list[tuple], *, live=None) -> dict:
    """Отчёт по товару аукциона.

    `rows` — история из storage.load_auction, `live` — котировка из свежего
    снимка (auction.Quote) с моментом снимка: (момент, Quote). Свежая точка
    добавляется к ряду в памяти и на диск не пишется — у истории аукциона
    один писатель, облачный сборщик.
    """
    rows = list(rows)
    if live is not None:
        moment, quote = live
        if not rows or moment > rows[-1][0]:
            rows.append((moment, quote.floor, quote.market, quote.quantity))
    series = frame.item_series(rows)
    if series.empty:
        return {"kind": "item", "empty": True}

    updated = series.index[-1]
    current = float(series.iloc[-1])
    windows = percentile.all_windows(
        series, current, percentile.ITEM_WINDOWS, honest_span=True
    )
    movement = percentile.trend(series, current)
    event_now = events.context()
    upcoming = events.upcoming(3)
    call, found_cycle = item.decide(
        series, current, windows, movement, event=event_now, upcoming=upcoming
    )
    season = seasonality.compute(series, robust=True)

    now = datetime.now(timezone.utc)
    span = (series.index[-1] - series.index[0]).days
    return {
        "kind": "item",
        "empty": False,
        "generated_at": now.isoformat(),
        "current": {
            "price": round(current, 2),
            "floor": round(rows[-1][1] / COPPER_PER_GOLD, 2),
            "updated_utc": updated.isoformat(),
            "updated_local": _local(updated),
            "age_minutes": round((now - updated.to_pydatetime()).total_seconds() / 60),
        },
        "history": {
            "points": int(len(series)),
            "since": series.index[0].isoformat(),
            "days": span,
        },
        "verdict": asdict(call),
        "trend": asdict(movement),
        "windows": [asdict(w) | {"spread_pct": round(w.spread_pct, 1)} for w in windows],
        "rhythm": _rhythm(season),
        "cycle": {k: v for k, v in found_cycle.items() if not k.startswith("_")}
        if found_cycle
        else None,
        "events": {"now": event_now, "upcoming": upcoming},
    }


def invalidate(region: str | None = None) -> None:
    """Сбросить кэш отчёта — после того как в историю легла новая точка."""
    if region is None:
        _cache.clear()
    else:
        _cache.pop(region, None)


def report(region: str = config.PRIMARY_REGION, *, fresh: bool = False) -> dict:
    cached = _cache.get(region)
    if cached and not fresh and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    built = _build(region)
    _cache[region] = (time.monotonic(), built)
    return built
