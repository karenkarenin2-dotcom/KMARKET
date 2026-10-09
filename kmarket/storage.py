"""Хранилище истории цен: один CSV на регион и месяц.

Почему CSV, а не база данных: файлы читаемы глазами, дифаются в git (сборщик
коммитит их прямо в репозиторий — это и есть наша «база»), а год замеров
каждые 10 минут — это ~52 тысячи строк, для pandas ничто. База здесь была бы
лишней деталью, которую пришлось бы где-то хостить.

Ключ дедупликации — updated_utc, время самой Blizzard. Мы опрашиваем ЧАЩЕ,
чем цена меняется (раз в 10 минут против ~20), поэтому большинство опросов
возвращает уже известную точку — её мы молча выбрасываем. Так частый опрос
защищает от пропусков крона, ничего не стоя в объёме данных.

Файл всегда переписывается целиком отсортированным и дедуплицированным:
на 4-5 тысячах строк это мгновенно, а git всё равно видит diff в одну строку.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .blizzard import TokenPrice

HEADER = ("updated_utc", "price_copper")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def month_file(region: str, moment: datetime, base: Path | None = None) -> Path:
    """data/history/eu/2026-07.csv (или тот же путь внутри другой базы)."""
    return (base or config.HISTORY_DIR) / region / f"{moment:%Y-%m}.csv"


def _format(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TIME_FORMAT)


def _parse(value: str) -> datetime:
    return datetime.strptime(value, TIME_FORMAT).replace(tzinfo=timezone.utc)


def read_month(path: Path) -> dict[datetime, int]:
    """Точки одного месяца: {момент UTC -> цена в меди}.

    ЛЮБАЯ нечитаемая строка пропускается молча. Это не лень, а требование
    надёжности, оплаченное поломкой 2026-07-26: git оставил в файле данных
    маркеры конфликта (`=======`, `>>>>>>> Stashed changes`), у таких строк
    нет второго поля, `int(None)` бросил TypeError — и ОДНА битая строка
    уронила весь дашборд целиком. Пропущенная точка стоит ничего, упавший
    дашборд стоит вечера отладки; ловим и TypeError тоже.
    """
    if not path.exists():
        return {}
    rows: dict[datetime, int] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                rows[_parse(row["updated_utc"])] = int(row["price_copper"])
            except (KeyError, ValueError, TypeError):
                continue
    return rows


def _write_month(path: Path, rows: dict[datetime, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".csv.tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for moment in sorted(rows):
            writer.writerow([_format(moment), rows[moment]])
    temp.replace(path)  # атомарно: обрыв на полпути не оставит огрызок


def append(price: TokenPrice, base: Path | None = None) -> bool:
    """Дописать замер в историю (по умолчанию — в git-историю сборщика)."""
    path = month_file(price.region, price.updated, base)
    rows = read_month(path)
    key = price.updated.astimezone(timezone.utc).replace(microsecond=0)
    if key in rows:
        return False
    rows[key] = price.price_copper
    _write_month(path, rows)
    return True


def append_live(price: TokenPrice) -> bool:
    """Дописать замер, снятый дашбордом, в ЛОКАЛЬНОЕ хранилище.

    ПОЧЕМУ ОТДЕЛЬНО ОТ data/history (поломка 2026-07-26). Дашборд писал
    живые цены прямо в git-историю, из-за чего рабочее дерево вечно было
    «грязным». Автопулл при старте (`git pull --rebase --autostash`)
    честно прятал эти правки в заначку, перебазировался и не мог вернуть
    её обратно — и вписывал МАРКЕРЫ КОНФЛИКТА внутрь CSV. История ломалась,
    дашборд падал, git застревал в состоянии `UU`.

    Теперь у двух писателей два разных места: облачный сборщик владеет
    data/history (коммитит его), дашборд пишет только в data/live (он в
    .gitignore). Пересекаться им больше нечем, а при чтении обе части
    склеиваются в load_history.
    """
    return append(price, base=config.LIVE_DIR)


# --------------------------------------------------------------------------
# Аукцион. Устройство то же, что у жетона (месячный CSV, атомарная замена,
# дедупликация по времени Blizzard), но ключ составной: в одном снимке
# приходит сотня предметов, и точку задаёт пара (момент, предмет).
#
# КОЛОНОК ПЯТЬ, И ЭТО НАМЕРЕННО (упрощение 2026-10-09). Раньше их было
# двенадцать: глубина дешёвого хвоста, стоимость выкупа, продажи, подвоз,
# очередь. Всё это обслуживало перекупку, которая при часовом снимке
# неисполнима (разобрано в CLAUDE.md). Старые файлы с лишними колонками
# читаются как прежде — лишнее просто игнорируется.
# --------------------------------------------------------------------------

AUCTION_HEADER = ("updated_utc", "item_id", "floor_copper", "market_copper", "quantity")

AuctionKey = tuple[datetime, int]
AuctionRow = tuple[int, int, int]  # floor, market, quantity


def auction_month_file(region: str, moment: datetime) -> Path:
    """data/auction/eu/2026-08.csv"""
    return config.AUCTION_DIR / region / f"{moment:%Y-%m}.csv"


def read_auction_month(path: Path) -> dict[AuctionKey, AuctionRow]:
    """Точки одного месяца. Битая строка пропускается молча — см. read_month."""
    if not path.exists():
        return {}
    rows: dict[AuctionKey, AuctionRow] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                key = (_parse(row["updated_utc"]), int(row["item_id"]))
                rows[key] = (
                    int(row["floor_copper"]),
                    int(row["market_copper"]),
                    int(row.get("quantity") or 0),
                )
            except (KeyError, ValueError, TypeError):
                continue
    return rows


def write_auction_month(path: Path, rows: dict[AuctionKey, AuctionRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".csv.tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(AUCTION_HEADER)
        for moment, item_id in sorted(rows):
            writer.writerow([_format(moment), item_id, *rows[(moment, item_id)]])
    temp.replace(path)


def append_auction(snapshot, item_ids: list[int]) -> int:
    """Дописать снимок по списку слежки. Возвращает число новых строк.

    Снимок целиком не пишем никогда: в нём 12 тысяч предметов, а нужна
    сотня с небольшим (см. kmarket.tracked).
    """
    path = auction_month_file(snapshot.region, snapshot.updated)
    rows = read_auction_month(path)
    moment = snapshot.updated.astimezone(timezone.utc).replace(microsecond=0)

    added = 0
    for item_id in item_ids:
        quote = snapshot.quotes.get(item_id)
        if quote is None:
            continue  # предмет пропал с аукциона — это не ошибка, а факт
        key = (moment, item_id)
        if key in rows:
            continue
        rows[key] = (quote.floor, quote.market, quote.quantity)
        added += 1

    if added:
        write_auction_month(path, rows)
    return added


def load_auction(
    region: str, item_ids: set[int] | None = None
) -> dict[int, list[tuple[datetime, int, int, int]]]:
    """История товаров: {предмет: [(момент, пол, рынок, количество), ...]}.

    Читает ВСЕ месяцы: товаров под слежкой сотня, а не четыреста, и год
    такой истории — десятки мегабайт, которые читаются за секунду. Обрезать
    историю незачем: ради неё проект и существует.
    """
    directory = config.AUCTION_DIR / region
    if not directory.exists():
        return {}
    rows: dict[AuctionKey, AuctionRow] = {}
    for path in sorted(directory.glob("*.csv")):
        rows.update(read_auction_month(path))
    result: dict[int, list[tuple[datetime, int, int, int]]] = {}
    for moment, item in sorted(rows):
        if item_ids is not None and item not in item_ids:
            continue
        result.setdefault(item, []).append((moment, *rows[(moment, item)]))
    return result


def load_history(region: str) -> list[tuple[datetime, int]]:
    """Вся история региона, отсортированная по времени.

    Три источника, от менее точного к более точному: внешний архив
    (разовый бутстрап) → history (замеры облачного сборщика) → live
    (замеры, снятые дашбордом только что). При совпадении момента
    побеждает последний, но значения там всё равно одинаковые: и сборщик,
    и дашборд берут цену из одного и того же ответа Blizzard.
    """
    rows: dict[datetime, int] = {}
    for base in (config.ARCHIVE_DIR, config.HISTORY_DIR, config.LIVE_DIR):
        directory = base / region
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.csv")):
            rows.update(read_month(path))
    return [(moment, rows[moment]) for moment in sorted(rows)]
