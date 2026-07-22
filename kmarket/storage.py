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


def month_file(region: str, moment: datetime) -> Path:
    """data/history/eu/2026-07.csv"""
    return config.HISTORY_DIR / region / f"{moment:%Y-%m}.csv"


def _format(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TIME_FORMAT)


def _parse(value: str) -> datetime:
    return datetime.strptime(value, TIME_FORMAT).replace(tzinfo=timezone.utc)


def read_month(path: Path) -> dict[datetime, int]:
    """Точки одного месяца: {момент UTC -> цена в меди}."""
    if not path.exists():
        return {}
    rows: dict[datetime, int] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                rows[_parse(row["updated_utc"])] = int(row["price_copper"])
            except (KeyError, ValueError):
                continue  # битую строку пропускаем, сбор важнее строгости
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


def append(price: TokenPrice) -> bool:
    """Дописать замер. Возвращает True, если точка новая."""
    path = month_file(price.region, price.updated)
    rows = read_month(path)
    key = price.updated.astimezone(timezone.utc).replace(microsecond=0)
    if key in rows:
        return False
    rows[key] = price.price_copper
    _write_month(path, rows)
    return True


def load_history(region: str) -> list[tuple[datetime, int]]:
    """Вся история региона, отсортированная по времени.

    Своя история (data/history) и внешний архив (data/archive) склеиваются;
    при совпадении момента побеждают НАШИ данные — они точнее по времени.
    """
    rows: dict[datetime, int] = {}
    archive = config.ARCHIVE_DIR / region
    if archive.exists():
        for path in sorted(archive.glob("*.csv")):
            rows.update(read_month(path))
    own = config.HISTORY_DIR / region
    if own.exists():
        for path in sorted(own.glob("*.csv")):
            rows.update(read_month(path))
    return [(moment, rows[moment]) for moment in sorted(rows)]
