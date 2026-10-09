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

    ПРО ИЗМЕРЕНИЕ ПРОДАЖ И ПРИТОКА. Предыдущий снимок держим в памяти и
    сравниваем id лотов: пропавшие — ушли с прилавка (sold_between),
    появившиеся ниже уровня восстановления — подрезали нас (added_between).

    ОДНОЙ ПАМЯТИ ПРОЦЕССА ОКАЗАЛОСЬ МАЛО (замерено 2026-08-09). Считалось,
    что отрезка хватит: сборщик живёт полчаса, а такт Blizzard часовой.
    На деле снимок обновляется около 41-й минуты часа, и граница попадала
    внутрь отрезка, только если тот стартовал в подходящие полчаса. За
    двое суток вышло 42 снимка и ДВА измерения — ни один товар не набрал
    минимума, и вся ветка расчётов, опирающаяся на скорость продаж, молча
    не работала.

    Поэтому лоты передаются следующему отрезку через файл (--state). В git
    он не попадает никогда: 17 тысяч id каждый час — это мегабайты мусора,
    и такое правило в проекте уже оплачено.
    """
    try:
        snapshot = auction.fetch(region, token, track_lots=set(ids))
    except Exception as error:  # noqa: BLE001 — наружу нужен текст, не стек
        print(f"[KMARKET] Снимок {region.upper()} не удался: {error}", file=sys.stderr)
        return -1, previous

    sold = fresh_supply = sold_hours = None
    if previous is not None and previous.updated != snapshot.updated:
        sold = auction.sold_between(previous, snapshot)
        fresh_supply = auction.added_between(previous, snapshot)
        sold_hours = (snapshot.updated - previous.updated).total_seconds() / 3600

    added = storage.append_auction(snapshot, ids, sold, sold_hours, fresh_supply)
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
        if fresh_supply:
            note += (
                f", ПОДВЕЗЛИ дешёвого: {sum(fresh_supply.values()):,} ед".replace(
                    ",", " "
                )
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
    parser.add_argument(
        "--state",
        default="",
        help="файл памяти о лотах прошлого отрезка (ВНЕ git; пусто — не хранить)",
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
    # Прошлый отрезок мог оставить нам свои лоты. Без этого измерение
    # продаж выходило только у того отрезка, внутрь которого случайно
    # попала 41-я минута часа, — и за двое суток набралось ровно два
    # измерения на 409 товаров (замерено 2026-08-09).
    previous: auction.Snapshot | None = None
    if args.state:
        previous = auction.load_lots(args.region, args.state)
        if previous is not None:
            print(
                f"[KMARKET] Помню прошлый снимок {previous.updated:%H:%M} UTC "
                f"({len(previous.lots)} лотов) — продажи измеримы сразу.",
                flush=True,
            )

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

    # Отдаём последний снимок следующему отрезку. Именно здесь, а не после
    # каждого опроса: файл нужен только на стыке процессов.
    if args.state and previous is not None and previous.lots:
        auction.save_lots(previous, args.state)

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
