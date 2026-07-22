"""Вердикт — то единственное, ради чего Карен открывает дашборд.

Профиль покупателя зафиксирован: **покупает жетон за золото и запасается
на месяцы вперёд**. Из этого следует вся асимметрия правил — дедлайна нет,
поэтому пропустить средненькую скидку не стоит ничего, а переплатить на
запасе из нескольких жетонов стоит сотни тысяч золота. Правила намеренно
осторожные: «брать» говорим редко и уверенно.

ПОРОГИ ВЗЯТЫ ИЗ ДАННЫХ, А НЕ С ПОТОЛКА, и первая же проверка опровергла
интуицию. Прогон backtest.stockpile по всей истории EU (5 полных лет,
профиль «набрать N жетонов за год») показал: порог 30-го перцентиля
стабильно ВЫИГРЫВАЕТ у порога 5-го — 13.7% против 9.3% экономии при шести
жетонах в год, и так на всех размерах закупки.

Причина в колонке forced_buys. Строгое правило срабатывает редко, набрать
нужное количество за год не выходит, и в декабре приходится докупать по
любой цене — эти вынужденные покупки съедают весь выигрыш от редких
идеальных входов. Мягкое правило даёт много возможностей, запас набирается
в дешёвые периоды, покупок под принуждением почти нет.

Практический вывод, зашитый в пороги: ЖДАТЬ ИДЕАЛЬНОГО ДНА — ХУДШАЯ
СТРАТЕГИЯ, ЧЕМ БРАТЬ, КОГДА ПРОСТО ЗАМЕТНО ДЕШЕВЛЕ. Меняя пороги,
обязательно перепроверять бэктестом, иначе вердикт превращается в гадание.

Вердикт ВСЕГДА объясняет себя списком причин: непрозрачный совет,
которому нельзя возразить, хуже отсутствия совета.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .percentile import CONTEXT_WINDOW, DECISION_WINDOW, Trend, WindowStats

BUY = "buy"
WAIT = "wait"
AVOID = "avoid"

# Пороги перцентиля в решающем окне (90 дней). Подобраны бэктестом —
# см. объяснение в шапке модуля, «жадный» порог 5 проигрывает.
BUY_PERCENTILE = 30.0
AVOID_PERCENTILE = 60.0

# Ниже этого перцентиля берём безусловно: на глубоком дне торговаться
# с рынком за лишний процент — та самая жадность, которая и проигрывает.
DEEP_BOTTOM = 15.0

# Контекст года: даже дешёвый по меркам квартала жетон может стоять
# в аномально дорогом году — тогда сигнал ослабляем.
CONTEXT_EXPENSIVE = 75.0


@dataclass
class Verdict:
    state: str
    title: str
    summary: str
    reasons: list[str] = field(default_factory=list)
    confidence: str = "средняя"


def _gold(value: float) -> str:
    """Число золота с неразрывными пробелами.

    Форматируем ЧИСЛО отдельно, а не заменяем запятые во всей фразе:
    так `.replace(",", " ")` однажды уже съел запятую-разделитель в самом
    предложении («51-й перцентиль диапазон …»).
    """
    return f"{value:,.0f}".replace(",", " ")


def _find(windows: list[WindowStats], days: int) -> WindowStats | None:
    return next((w for w in windows if w.days == days), None)


def _confidence(decision: WindowStats | None, gap_hours: float | None) -> str:
    if decision is None or decision.points < 60:
        return "низкая"
    if gap_hours is not None and gap_hours > 48:
        return "низкая"
    if decision.points < 300:
        return "средняя"
    return "высокая"


def decide(
    current: float,
    windows: list[WindowStats],
    trend: Trend,
    *,
    max_gap_hours: float | None = None,
) -> Verdict:
    decision = _find(windows, DECISION_WINDOW)
    context = _find(windows, CONTEXT_WINDOW)
    confidence = _confidence(decision, max_gap_hours)

    if decision is None:
        return Verdict(
            state=WAIT,
            title="ЖДАТЬ",
            summary="Данных пока мало для вывода",
            reasons=["История ещё не набрала 90 дней — перцентиль считать не на чем."],
            confidence="низкая",
        )

    percentile = decision.percentile
    reasons: list[str] = [
        f"Цена дешевле, чем в {100 - percentile:.0f}% моментов за последние 90 дней "
        f"(перцентиль {percentile:.0f})."
    ]

    if percentile <= BUY_PERCENTILE:
        state = BUY
    elif percentile <= AVOID_PERCENTILE:
        state = WAIT
    else:
        state = AVOID

    # Контекст года: дно квартала внутри дорогого года — это не дно.
    if context is not None:
        reasons.append(
            f"За год: {context.percentile:.0f}-й перцентиль, "
            f"диапазон {_gold(context.low)}–{_gold(context.high)} g."
        )
        if state == BUY and context.percentile > CONTEXT_EXPENSIVE:
            state = WAIT
            reasons.append(
                "Но по меркам года цена всё ещё высокая — дешевизна только на фоне "
                "дорогого квартала. Для запаса впрок этого мало."
            )

    # Тренд корректирует момент, но не переворачивает вывод.
    if percentile <= DEEP_BOTTOM and state == BUY:
        reasons.append(
            f"Это глубокое дно ({percentile:.0f}-й перцентиль) — торговаться за лишний "
            f"процент здесь и есть та жадность, которая по бэктесту проигрывает."
        )
    elif trend.direction == "падает" and state == BUY:
        state = WAIT
        reasons.append(
            "Цена активно падает прямо сейчас — есть смысл дать падению закончиться."
        )
    elif trend.direction == "растёт" and state == WAIT and percentile <= 45:
        state = BUY
        reasons.append(
            "Цена уже разворачивается вверх с низкой базы — окно закрывается."
        )
    reasons.extend(trend.notes)

    if decision.spread_pct < 8:
        reasons.append(
            f"Рынок спокойный: размах за 90 дней всего {decision.spread_pct:.0f}% — "
            f"выигрыш от идеального тайминга невелик в любом случае."
        )

    titles = {
        BUY: ("БРАТЬ", "Хороший момент для запаса"),
        WAIT: ("ЖДАТЬ", "Момент неплохой, но лучше бывает"),
        AVOID: ("НЕ СЕЙЧАС", "Дороже среднего — запасаться невыгодно"),
    }
    title, summary = titles[state]
    if state == AVOID:
        cheaper = decision.low
        reasons.append(
            f"За 90 дней жетон падал до {_gold(cheaper)} g — это на "
            f"{(current / cheaper - 1) * 100:.0f}% дешевле текущей цены."
        )

    return Verdict(
        state=state, title=title, summary=summary, reasons=reasons, confidence=confidence
    )
