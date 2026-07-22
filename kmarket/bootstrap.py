"""Разовый бутстрап истории цены жетона из внешнего архива.

ЗАЧЕМ. Blizzard истории не отдаёт, а наш сборщик начал копить её только
сегодня. Перцентили, сезонность и бэктест на двух точках не считаются никак —
поэтому один раз подтягиваем чужой архив, чтобы аналитика заработала сразу,
а не через полгода.

ИСТОЧНИК. data.wowtoken.app — публичный JSON, которым питается сайт
wowtoken.app (фронтенд открыт: github.com/sneaky-emily/wowtoken.app).
Классический wowtokenprices.com, на который ссылается половина интернета,
на 2026-07-22 мёртв — TLS-рукопожатие обрывается.

ПОЧЕМУ СЛОЯМИ. Источник прореживает длинные периоды: `all` — это 5.7 лет
с шагом 6.5 часов, зато `1m` — полные 20 минут, такт самой Blizzard.
Поэтому берём все периоды от грубого к точному и объединяем: на свежей
части остаётся высокое разрешение (нужно для сезонности по часам), на
старой — редкие, но настоящие точки (их хватает для перцентилей).

ЧЕСТНОСТЬ. Архив кладётся в data/archive/, ОТДЕЛЬНО от собственных
замеров в data/history/. При склейке в storage.load_history наши данные
побеждают — они точнее по времени. Всегда видно, где чьё.

Запускается руками, один раз, НЕ по расписанию:

    python -m kmarket.bootstrap
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from . import config, storage
from .blizzard import COPPER_PER_GOLD

SOURCE_URL = "https://data.wowtoken.app/v2/relative/retail/{region}/{period}.json"
SOURCE_NAME = "data.wowtoken.app"

# От грубого к точному: каждый следующий слой уточняет свежую часть.
PERIODS = ("all", "2y", "1y", "6m", "3m", "1m", "1d")

TIMEOUT = 90


def _fetch(region: str, period: str) -> list[tuple[datetime, int]]:
    """[["2026-07-22T09:43:30+00:00", 364429], ...] -> [(момент UTC, медь)]"""
    url = SOURCE_URL.format(region=region, period=period)
    request = urllib.request.Request(url, headers={"User-Agent": "KMARKET/0.1 (one-time bootstrap)"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        raw = json.loads(response.read().decode("utf-8"))

    points: list[tuple[datetime, int]] = []
    for moment, gold in raw:
        stamp = datetime.fromisoformat(moment).astimezone(timezone.utc).replace(microsecond=0)
        # Источник отдаёт ЗОЛОТО, мы храним МЕДЬ. Потерь нет: цена жетона
        # всегда целое число золота (проверено на живых данных).
        points.append((stamp, int(gold) * COPPER_PER_GOLD))
    return points


def collect_region(region: str) -> dict[datetime, int]:
    merged: dict[datetime, int] = {}
    for period in PERIODS:
        try:
            points = _fetch(region, period)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            print(f"  {period:>3}: пропущен ({error})", file=sys.stderr)
            continue
        before = len(merged)
        merged.update(points)
        print(f"  {period:>3}: {len(points):>5} точек, новых {len(merged) - before:>5}")
    return merged


def verify(region: str, archive: dict[datetime, int]) -> None:
    """Сверка архива с нашими собственными замерами там, где они пересекаются.

    Если внешний источник меряет не то же самое (другой регион, другая
    версия игры, другая единица) — это вылезет именно здесь.
    """
    own = dict(storage.load_history(region))
    common = sorted(set(own) & set(archive))
    if not common:
        print(f"  сверка: пересечений с нашими замерами пока нет (это нормально в первый день)")
        return
    mismatches = [m for m in common if own[m] != archive[m]]
    print(f"  сверка: {len(common)} общих точек, расхождений {len(mismatches)}")
    for moment in mismatches[:5]:
        print(f"    {moment:%Y-%m-%d %H:%M}  наш {own[moment] // COPPER_PER_GOLD:,}"
              f"  архив {archive[moment] // COPPER_PER_GOLD:,}".replace(",", " "))


def write_archive(region: str, points: dict[datetime, int]) -> int:
    by_month: dict[str, dict[datetime, int]] = defaultdict(dict)
    for moment, copper in points.items():
        by_month[f"{moment:%Y-%m}"][moment] = copper

    directory = config.ARCHIVE_DIR / region
    directory.mkdir(parents=True, exist_ok=True)
    for month, rows in by_month.items():
        storage._write_month(directory / f"{month}.csv", rows)
    return len(by_month)


def write_source_note(fetched: datetime) -> None:
    (config.ARCHIVE_DIR / "SOURCE.md").write_text(
        f"""# Источник архива

Данные в этой папке — НЕ наши замеры. Это разовый бутстрап истории,
скачанный один раз, чтобы аналитика заработала сразу, а не через полгода.

- **Источник:** [{SOURCE_NAME}](https://wowtoken.app/) — публичный JSON,
  которым питается сайт wowtoken.app.
- **Скачано:** {fetched:%Y-%m-%d %H:%M} UTC, одним проходом, повторно не тянем.
- **Периоды:** {", ".join(PERIODS)} — склеены слоями, точный поверх грубого.
- **Единицы:** источник отдаёт золото, здесь пересчитано в медь (×10 000),
  как во всей остальной истории.

Собственные замеры KMARKET лежат в `data/history/` и при склейке
**побеждают** архив на совпадающих моментах: они точнее по времени.
""",
        encoding="utf-8",
    )


def main() -> int:
    fetched = datetime.now(timezone.utc)
    for region in config.REGIONS:
        print(f"[KMARKET] Архив {region.upper()} из {SOURCE_NAME}:")
        points = collect_region(region)
        if not points:
            print(f"  ничего не скачалось — регион пропущен", file=sys.stderr)
            continue
        verify(region, points)
        months = write_archive(region, points)
        oldest, newest = min(points), max(points)
        print(
            f"  итого {len(points)} точек в {months} файлах, "
            f"{oldest:%Y-%m-%d} .. {newest:%Y-%m-%d}\n"
        )
    write_source_note(fetched)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
