"""Сборщик товарного аукциона: часовой снимок commodities → история цен.

Ноль зависимостей, как и сборщик жетона.

    python -m kmarket.collect_auction              # один снимок
    python -m kmarket.collect_auction --minutes 35 # окно (так в Actions)

ЧТО ПИШЕТСЯ. Товары, выбранные Кареном (`data/tracked.json`), плюс тихий
фон реагентов Midnight (`data/reagents.json`) — см. kmarket.tracked.

ПОЧЕМУ ОТДЕЛЬНЫЙ СБОРЩИК, А НЕ ВЕТКА В collect.py. У аукциона свой такт:
Blizzard пересчитывает commodities раз в ЧАС, а жетон — раз в 20 минут.
Смешав их, мы либо качали бы 3 МБ каждые десять минут впустую, либо
проредили бы жетон.

ПОЧЕМУ ТУТ ТОЖЕ ОКНО. Та же беда, что у жетона (см. шапку collect.py):
крон Actions исполняется раз в 1–3 часа вместо заказанного. Окно опроса
ловит смену снимка внутри своего часа, дубли отсекаются по времени
Blizzard.
"""

from __future__ import annotations

import argparse
import sys
import time

from . import auction, config, storage, tracked
from .blizzard import get_access_token

DEFAULT_INTERVAL = 600.0


def poll_once(token: str, region: str, ids: list[int]) -> int:
    """Один снимок. Возвращает число новых строк или -1 при отказе."""
    try:
        snapshot = auction.fetch(region, token)
    except Exception as error:  # noqa: BLE001 — наружу нужен текст, не стек
        print(f"[KMARKET] Снимок {region.upper()} не удался: {error}", file=sys.stderr)
        return -1
    added = storage.append_auction(snapshot, ids)
    print(
        f"[KMARKET] {region.upper()} аукцион: {snapshot.updated:%Y-%m-%d %H:%M} UTC, "
        f"предметов в снимке {len(snapshot.quotes)}, новых строк {added}",
        flush=True,
    )
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сбор товарного аукциона WoW")
    parser.add_argument("--minutes", type=float, default=0.0, help="длительность окна опроса")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="пауза, секунд")
    parser.add_argument("--region", default=config.PRIMARY_REGION)
    # parse_known_args, а не parse_args — НАМЕРЕННО. Уже запущенный в облаке
    # workflow старой версии может после проигранной гонки за push взять
    # НОВЫЙ код и вызвать его со старыми флагами (--wide-hours, --state).
    # Падение на незнакомом флаге молча остановило бы сбор до следующего
    # запуска. Лишний флаг — повод для строчки в логе, не для отказа.
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        print(f"[KMARKET] Игнорирую устаревшие флаги: {' '.join(unknown)}", flush=True)

    ids = tracked.collect_ids()
    if not ids:
        print("[KMARKET] Список слежки пуст — писать нечего.", file=sys.stderr)
        return 1

    try:
        token = get_access_token()
    except Exception as error:  # noqa: BLE001
        print(f"[KMARKET] Не удалось получить токен: {error}", file=sys.stderr)
        return 1

    deadline = time.monotonic() + args.minutes * 60
    polls = failures = added = 0
    while True:
        result = poll_once(token, args.region, ids)
        polls += 1
        if result < 0:
            failures += 1
        else:
            added += result
        if deadline - time.monotonic() <= args.interval:
            break
        time.sleep(args.interval)

    if args.minutes:
        print(
            f"[KMARKET] Окно закрыто: снимков {polls}, отказов {failures}, "
            f"новых строк {added}.",
            flush=True,
        )
    # Красным только полный провал: одиночный отказ — обычная жизнь сети.
    return 1 if failures == polls else 0


if __name__ == "__main__":
    raise SystemExit(main())
