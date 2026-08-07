"""Сборщик товарного аукциона: снимок commodities → история по списку слежки.

Ноль зависимостей, как и сборщик жетона.

    python -m kmarket.collect_auction              # один снимок
    python -m kmarket.collect_auction --minutes 55 # окно (так в Actions)

ПОЧЕМУ ОТДЕЛЬНЫЙ СБОРЩИК, А НЕ ВЕТКА В collect.py. У аукциона свой такт:
Blizzard пересчитывает commodities раз в ЧАС, а жетон — раз в 20 минут.
Смешав их, мы либо качали бы 3 МБ каждые десять минут впустую, либо
проредили бы жетон. Разные такты — разные сборщики и разные workflow.

ПОЧЕМУ ТУТ ТОЖЕ ОКНО. Та же беда, что у жетона (см. шапку collect.py):
крон Actions исполняется раз в 1–3 часа вместо заказанного. Для часовых
данных это значит прямые пропуски снимков. Окно опроса ловит смену
`last-modified` внутри своего часа, а дубли отсекаются по нему же.

ПОЧЕМУ ПАУЗА ДЛИННАЯ. Снимок весит 2.9 МБ сжатых, и дёргать его каждые
пять минут — впустую гонять сотни мегабайт за окно. Проверять раз в
10 минут более чем достаточно, чтобы не проспать часовое обновление.
"""

from __future__ import annotations

import argparse
import sys
import time

from . import auction, config, storage, watchlist
from .blizzard import get_access_token

DEFAULT_INTERVAL = 600.0

# Дата, после которой широкий срез выключается САМ.
#
# Он заведён под цикл патча 12.1 (12 августа) и сезона 2 (18 августа) и
# стоит 2.3 МБ в сутки. Такие временные меры никто никогда не выключает
# руками — их просто забывают, и через год выясняется, что репозиторий
# распух на 25 гигабайт. Поэтому у меры есть срок, зашитый в код, а не
# только в комментарии к workflow. Продлить — поменять дату осознанно.
WIDE_UNTIL = "2026-09-06"


def poll_once(
    token: str,
    region: str,
    ids: list[int],
    wide_hours: float = 0.0,
    previous: auction.Snapshot | None = None,
) -> tuple[int, auction.Snapshot | None]:
    """Один снимок. Возвращает (число новых строк, снимок) или (-1, previous).

    Широкий срез пишется из ЭТОГО ЖЕ ответа, а не отдельным запросом:
    снимок уже скачан и разобран, всё нужное в нём есть.

    ПРО ИЗМЕРЕНИЕ ПРОДАЖ. Предыдущий снимок держим в памяти и сравниваем
    id лотов — так видно, что именно ушло с прилавка (см. auction.sold_between).
    Файла состояния для этого не нужно: сборщик и так живёт целое окно в
    55 минут, а часовой такт Blizzard попадает внутрь этого окна. Хранить
    17 тысяч id между запусками в git означало бы гнать туда мегабайты
    мусора каждый час.
    """
    try:
        snapshot = auction.fetch(region, token, track_lots=set(ids))
    except Exception as error:  # noqa: BLE001 — наружу нужен текст, не стек
        print(f"[KMARKET] Снимок {region.upper()} не удался: {error}", file=sys.stderr)
        return -1, previous

    sold = sold_hours = None
    if previous is not None and previous.updated != snapshot.updated:
        sold = auction.sold_between(previous, snapshot)
        sold_hours = (snapshot.updated - previous.updated).total_seconds() / 3600

    added = storage.append_auction(snapshot, ids, sold, sold_hours)
    note = ""
    if wide_hours and f"{snapshot.updated:%Y-%m-%d}" > WIDE_UNTIL:
        note = f", широкий срез отключён (срок вышел {WIDE_UNTIL})"
    elif wide_hours and storage.wide_due(region, snapshot.updated, wide_hours):
        wide = storage.append_auction_wide(snapshot)
        if wide:
            note = f", ШИРОКИЙ СРЕЗ: {wide} предметов"
    if sold is not None:
        note += (
            f", ПРОДАНО за {sold_hours:.1f} ч: "
            f"{sum(sold.values()):,} ед по {len(sold)} предметам".replace(",", " ")
        )
    print(
        f"[KMARKET] {region.upper()} аукцион: {snapshot.updated:%Y-%m-%d %H:%M} UTC, "
        f"предметов в снимке {len(snapshot.quotes)}, новых строк {added}{note}",
        flush=True,
    )
    return added, snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сбор товарного аукциона WoW")
    parser.add_argument("--minutes", type=float, default=0.0, help="длительность окна опроса")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="пауза, секунд")
    parser.add_argument("--region", default=config.PRIMARY_REGION)
    parser.add_argument(
        "--wide-hours",
        type=float,
        default=0.0,
        help="как часто писать ВЕСЬ аукцион, часов (0 — не писать)",
    )
    args = parser.parse_args(argv)

    ids = watchlist.item_ids()
    if not ids:
        print(
            "[KMARKET] Список слежки пуст. Собери его: python -m kmarket.watchlist",
            file=sys.stderr,
        )
        return 1

    try:
        token = get_access_token()
    except Exception as error:  # noqa: BLE001
        print(f"[KMARKET] Не удалось получить токен: {error}", file=sys.stderr)
        return 1

    deadline = time.monotonic() + args.minutes * 60
    polls = failures = added = 0
    previous: auction.Snapshot | None = None

    while True:
        result, previous = poll_once(
            token, args.region, ids, args.wide_hours, previous
        )
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
