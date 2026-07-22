"""Бэктест правил покупки: сколько золота правило реально сэкономило бы.

Смысл модуля — не дать поверить в красивую идею без проверки. Любой совет
вида «бери, когда дёшево» обязан отвечать на вопрос «а насколько это лучше,
чем купить в случайный день?».

ТРИ ЧЕСТНОСТИ, без которых бэктест — самообман:

1. НИКАКОГО ЗАГЛЯДЫВАНИЯ В БУДУЩЕЕ ПРИ РЕШЕНИИ. Перцентиль на день D
   считается ТОЛЬКО по данным строго до D. (В ОЦЕНКЕ результата будущее
   использовать можно и нужно — мы измеряем, а не решаем.)

2. СРАВНИВАТЬ НАДО С ЛОКАЛЬНЫМ УРОВНЕМ, А НЕ СО СРЕДНЕЙ ЗА ВСЮ ИСТОРИЮ.
   Это не педантизм, а пойманная ошибка: цена жетона выросла со 153 тыс.
   в 2020-м до 370 тыс. в 2026-м, и правило «покупай в нижних 5% за
   полгода» в растущем рынке срабатывает в основном в РАННИЕ, дешёвые
   годы. Сравнение со средней за пять лет показывало «экономию 12%»,
   которая на деле означала «купил в 2021-м». Поэтому главная метрика —
   насколько цена покупки ниже уровня ВОКРУГ ТОЙ ЖЕ ДАТЫ (±45 дней).
   Тренд сокращается, остаётся чистое качество тайминга.

3. ЧАСТОТА СИГНАЛОВ ВАЖНА НЕ МЕНЬШЕ ВЫГОДЫ. Правило, экономящее 15%, но
   заставляющее ждать два года, бесполезно. Поэтому считаем не только
   среднее ожидание, но и типичное (медиана) и худшее (максимум).

Дневная медиана, а не минимум дня: минимум предполагает, что ты поймал
ровно лучшую минуту суток. Медиана — «купил в обычный момент тех суток».
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import pandas as pd

from .percentile import percentile_of

# Полуширина окна локального сравнения. ±45 дней — достаточно, чтобы
# усреднить рыночный шум, и достаточно мало, чтобы годовой тренд внутри
# окна был незначителен.
LOCAL_HALF_WINDOW = 45


@dataclass
class BacktestResult:
    window_days: int
    threshold: float
    signals: int
    signals_per_year: float
    # ГЛАВНАЯ метрика: насколько дешевле локального уровня покупали, %
    local_advantage_pct: float | None
    # Вспомогательная и предвзятая в трендовом рынке — только для контекста
    period_saving_pct: float | None
    avg_signal_price: float | None
    median_wait_days: int | None
    max_wait_days: int | None
    tested_days: int

    @property
    def practical(self) -> bool:
        """Годится ли правило для жизни: даёт выигрыш и не морит ожиданием."""
        if self.local_advantage_pct is None or self.median_wait_days is None:
            return False
        return self.local_advantage_pct >= 1.0 and self.median_wait_days <= 30


@dataclass
class MonthlyResult:
    """Симуляция «покупаю по жетону каждый месяц».

    Самая понятная проверка: сравнение идёт ВНУТРИ каждого месяца, поэтому
    долгосрочный рост цены не влияет на результат вообще. Если сигнала за
    месяц не было — покупка всё равно совершается в последний день, как в
    жизни. Правило не может «отсидеться».
    """

    window_days: int
    threshold: float
    months: int
    months_with_signal: int
    avg_strategy_price: float
    avg_random_price: float
    saving_pct: float
    saving_gold: float


def daily_median(series: pd.Series) -> pd.Series:
    """Одна цена на день — «сколько стоило в обычный момент тех суток»."""
    return series.resample("1D").median().dropna()


def _signal_days(daily: pd.Series, window_days: int, threshold: float) -> tuple[list, list]:
    """Дни срабатывания правила и дни, на которых правило вообще проверялось."""
    warmup = pd.Timedelta(days=window_days)
    signals: list[pd.Timestamp] = []
    tested: list[pd.Timestamp] = []
    for moment, price in daily.items():
        # СТРОГО прошлое: верхняя граница исключает сам день.
        past = daily.loc[moment - warmup : moment - pd.Timedelta(seconds=1)]
        if len(past) < window_days * 0.5:
            continue
        tested.append(moment)
        if percentile_of(past, float(price)) <= threshold:
            signals.append(moment)
    return signals, tested


def _local_advantage(daily: pd.Series, signals: list) -> float | None:
    """Средняя скидка к уровню ВОКРУГ той же даты, в процентах."""
    half = pd.Timedelta(days=LOCAL_HALF_WINDOW)
    advantages: list[float] = []
    for moment in signals:
        around = daily.loc[moment - half : moment + half]
        if len(around) < 20:
            continue
        baseline = float(around.mean())
        if baseline:
            advantages.append((1 - float(daily[moment]) / baseline) * 100)
    return round(statistics.fmean(advantages), 2) if advantages else None


def run(series: pd.Series, *, window_days: int = 90, threshold: float = 20.0) -> BacktestResult:
    """Правило: покупай, когда цена в нижних `threshold`% за последние `window_days`."""
    daily = daily_median(series)
    signals, tested = _signal_days(daily, window_days, threshold)

    if not tested:
        return BacktestResult(window_days, threshold, 0, 0.0, None, None, None, None, None, 0)

    tested_days = (tested[-1] - tested[0]).days or 1
    baseline = float(daily.loc[tested[0] : tested[-1]].mean())

    if not signals:
        return BacktestResult(
            window_days, threshold, 0, 0.0, None, None, None, None, None, tested_days
        )

    prices = [float(daily[m]) for m in signals]
    average = statistics.fmean(prices)
    # Ожидание считаем с краёв тоже: иначе редкое правило выглядит удобным.
    edges = [tested[0], *signals, tested[-1]]
    waits = [(b - a).days for a, b in zip(edges, edges[1:])]

    return BacktestResult(
        window_days=window_days,
        threshold=threshold,
        signals=len(signals),
        signals_per_year=round(len(signals) / tested_days * 365, 1),
        local_advantage_pct=_local_advantage(daily, signals),
        period_saving_pct=round((1 - average / baseline) * 100, 2),
        avg_signal_price=round(average),
        median_wait_days=int(statistics.median(waits)),
        max_wait_days=int(max(waits)),
        tested_days=tested_days,
    )


def monthly(
    series: pd.Series, *, window_days: int = 90, threshold: float = 20.0
) -> MonthlyResult | None:
    """«Покупаю жетон раз в месяц» — по сигналу против случайного дня."""
    daily = daily_median(series)
    signals, tested = _signal_days(daily, window_days, threshold)
    if not tested:
        return None

    signal_set = set(signals)
    scope = daily.loc[tested[0] : tested[-1]]

    strategy: list[float] = []
    random_day: list[float] = []
    with_signal = 0

    for _, month in scope.groupby(pd.Grouper(freq="MS")):
        if month.empty:
            continue
        hits = [m for m in month.index if m in signal_set]
        if hits:
            strategy.append(float(month[hits[0]]))  # покупаем на первом сигнале
            with_signal += 1
        else:
            strategy.append(float(month.iloc[-1]))  # сигнала не было — берём в конце
        random_day.append(float(month.mean()))  # покупка в случайный день месяца

    if not strategy:
        return None

    strategy_avg = statistics.fmean(strategy)
    random_avg = statistics.fmean(random_day)
    return MonthlyResult(
        window_days=window_days,
        threshold=threshold,
        months=len(strategy),
        months_with_signal=with_signal,
        avg_strategy_price=round(strategy_avg),
        avg_random_price=round(random_avg),
        saving_pct=round((1 - strategy_avg / random_avg) * 100, 2),
        saving_gold=round(random_avg - strategy_avg),
    )


@dataclass
class StockpileResult:
    """Симуляция профиля Карена: «запасаюсь впрок, могу ждать месяцами».

    Ключевое отличие от `monthly`: покупку МОЖНО переносить через месяцы,
    поэтому вынужденных дорогих покупок почти нет — надо лишь набрать
    нужное число жетонов за год. Сравнение идёт ВНУТРИ каждого года
    (покупка по сигналам против покупки 1-го числа каждого месяца),
    поэтому долгосрочный рост цены на результат не влияет.
    """

    window_days: int
    threshold: float
    per_year: int
    years: int
    avg_strategy_price: float
    avg_even_price: float
    saving_pct: float
    saving_gold: float
    forced_buys: int  # покупки «в конце года», когда сигналов не хватило


def stockpile(
    series: pd.Series,
    *,
    window_days: int = 90,
    threshold: float = 20.0,
    per_year: int = 12,
) -> StockpileResult | None:
    """Набрать `per_year` жетонов за год — по сигналам против равномерной покупки."""
    daily = daily_median(series)
    signals, tested = _signal_days(daily, window_days, threshold)
    if not tested:
        return None

    signal_set = set(signals)
    scope = daily.loc[tested[0] : tested[-1]]

    strategy_prices: list[float] = []
    even_prices: list[float] = []
    forced = 0
    years = 0

    for _, chunk in scope.groupby(scope.index.year):
        if len(chunk) < 300:  # неполный год сравнивать нечестно
            continue
        years += 1
        hits = [m for m in chunk.index if m in signal_set][:per_year]
        bought = [float(chunk[m]) for m in hits]
        # Сигналов не хватило — доборы в конце года, как в жизни.
        while len(bought) < per_year:
            bought.append(float(chunk.iloc[-1]))
            forced += 1
        strategy_prices.extend(bought)
        # База: покупка 1-го числа каждого месяца, без всякой аналитики.
        even_prices.extend(
            float(month.iloc[0]) for _, month in chunk.groupby(pd.Grouper(freq="MS")) if len(month)
        )

    if not strategy_prices or not even_prices:
        return None

    strategy_avg = statistics.fmean(strategy_prices)
    even_avg = statistics.fmean(even_prices)
    return StockpileResult(
        window_days=window_days,
        threshold=threshold,
        per_year=per_year,
        years=years,
        avg_strategy_price=round(strategy_avg),
        avg_even_price=round(even_avg),
        saving_pct=round((1 - strategy_avg / even_avg) * 100, 2),
        saving_gold=round(even_avg - strategy_avg),
        forced_buys=forced,
    )


def grid(
    series: pd.Series,
    windows: tuple[int, ...] = (30, 60, 90, 180),
    thresholds: tuple[float, ...] = (5, 10, 15, 20, 30),
) -> list[BacktestResult]:
    """Перебор правил — чтобы выбирать пороги по данным, а не по вкусу."""
    return [run(series, window_days=w, threshold=t) for w in windows for t in thresholds]


def best(results: list[BacktestResult]) -> BacktestResult | None:
    """Лучшее ПРИГОДНОЕ правило: максимум выигрыша среди не изматывающих ожиданием."""
    usable = [r for r in results if r.practical]
    if not usable:
        return None
    return max(usable, key=lambda r: r.local_advantage_pct or 0)
