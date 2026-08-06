"""Сборщик: снять текущую цену жетона по всем регионам и дописать в историю.

Локально — одним выстрелом:

    python -m kmarket.collect

В Actions — циклом на всё окно запуска (см. ПОЧЕМУ ЦИКЛ ниже):

    python -m kmarket.collect --minutes 9 --interval 300

Коды возврата: 0 — всё хорошо (в том числе когда новых точек нет, это норма),
1 — API не ответил ни разу за весь цикл. Ненулевой код красит запуск в
Actions красным, чтобы молчаливая поломка сбора не осталась незамеченной:
восстановить пропущенные часы потом будет нечем.

ПОЧЕМУ ЦИКЛ, А НЕ ОДИН ЗАМЕР НА ЗАПУСК (измерено 2026-08-06).
Крон `*/10` в GitHub Actions — это пожелание, а не расписание. По факту
планировщик будил нас раз в 1–3 часа: за 16 суток в историю EU легло
12.9 точки в сутки вместо 72 возможных, то есть 82% истории потеряно
безвозвратно. Хуже того, когда планировщик после простоя выстреливал
несколькими отложенными запусками разом, группа `concurrency` глушила
все, кроме одного (в списке они видны как `cancelled` с нулём шагов) —
мы выбрасывали ровно те замеры, ради которых залп и случался.

Лечение: не «один запуск — один замер», а «один запуск — целое окно
замеров». Проснувшись, сборщик опрашивает API каждые 5 минут в течение
отведённого времени. Такт Blizzard — 20 минут, поэтому за час набегает
три новые точки вместо одной, и редкость пробуждений перестаёт быть
приговором. Минуты Actions на публичном репозитории бесплатны — ровно
за этим он и сделан публичным.

ОТКАЗ ВНУТРИ ЦИКЛА НЕ ПРЕРЫВАЕТ ЦИКЛ. Blizzard моргает (наблюдался
случайный HTTP 500), и один неудачный опрос не повод бросать оставшиеся
пятьдесят минут окна. Ошибкой считается только окно, в котором не удалось
НИ ОДНОГО опроса.
"""

from __future__ import annotations

import argparse
import sys
import time

from . import config, storage
from .blizzard import fetch_all


def poll_once() -> int:
    """Один опрос всех регионов. Возвращает число НОВЫХ точек, -1 при отказе."""
    try:
        prices = fetch_all(config.REGIONS)
    except Exception as error:  # noqa: BLE001 — наружу нужен внятный текст, не стек
        print(f"[KMARKET] Сбор не удался: {error}", file=sys.stderr)
        return -1

    fresh = 0
    for price in prices:
        is_new = storage.append(price)
        fresh += int(is_new)
        mark = "новая" if is_new else "уже есть"
        print(
            f"[KMARKET] {price.region.upper()}: {price.gold:,} g".replace(",", " ")
            + f"  ({price.updated:%Y-%m-%d %H:%M} UTC, {mark})",
            flush=True,  # без flush порядок строк в логе Actions разъезжается
        )
    return fresh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сбор цены жетона WoW")
    parser.add_argument(
        "--minutes",
        type=float,
        default=0.0,
        help="сколько минут держать окно опроса (0 — один замер и выход)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=300.0,
        help="пауза между опросами внутри окна, секунд",
    )
    args = parser.parse_args(argv)

    deadline = time.monotonic() + args.minutes * 60
    polls = failures = fresh = 0

    while True:
        result = poll_once()
        polls += 1
        if result < 0:
            failures += 1
        else:
            fresh += result

        remaining = deadline - time.monotonic()
        # Спим, только если после сна успеем сделать ещё один осмысленный опрос.
        if remaining <= args.interval:
            break
        time.sleep(args.interval)

    if args.minutes:
        print(
            f"[KMARKET] Окно закрыто: опросов {polls}, отказов {failures}, "
            f"новых точек {fresh}.",
            flush=True,
        )

    # Провал — только когда не удалось вообще ничего. Одиночные отказы
    # внутри окна это нормальная жизнь сети, а не повод красить запуск.
    return 1 if failures == polls else 0


if __name__ == "__main__":
    raise SystemExit(main())
