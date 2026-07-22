"""Сборщик: снять текущую цену жетона по всем регионам и дописать в историю.

Запускается GitHub Actions по крону каждые 10 минут, а локально — вручную:

    python -m kmarket.collect

Коды возврата: 0 — всё хорошо (в том числе когда новых точек нет, это норма),
1 — API не ответил. Ненулевой код красит запуск в Actions красным, чтобы
молчаливая поломка сбора не осталась незамеченной: восстановить пропущенные
часы потом будет нечем.
"""

from __future__ import annotations

import sys

from . import config, storage
from .blizzard import fetch_all


def main() -> int:
    try:
        prices = fetch_all(config.REGIONS)
    except Exception as error:  # noqa: BLE001 — наружу нужен внятный текст, не стек
        print(f"[KMARKET] Сбор не удался: {error}", file=sys.stderr)
        return 1

    for price in prices:
        is_new = storage.append(price)
        mark = "новая" if is_new else "уже есть"
        print(
            f"[KMARKET] {price.region.upper()}: {price.gold:,} g".replace(",", " ")
            + f"  ({price.updated:%Y-%m-%d %H:%M} UTC, {mark})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
