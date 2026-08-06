"""Список слежки: какие предметы аукциона мы вообще записываем в историю.

Ноль зависимостей.

ПОЧЕМУ НЕ ВСЁ ПОДРЯД. В снимке 12 тысяч предметов. Писать их все — это
около 100 МБ истории в месяц, репозиторий станет неподъёмным, а девять
десятых строк будут про предметы, которыми никто не торгует. Пишем
десятки, а не тысячи.

ПОЧЕМУ СПИСОК АВТОМАТИЧЕСКИЙ, А НЕ РУЧНОЙ. Ручной список ремесленных
товаров пришлось бы пополнять руками после каждого патча — ровно та
грабля, на которой уже стоит `events.EVENTS`, где протухание самого
ценного сигнала происходит МОЛЧА. Патч 12.1 приносит реагенты Coiled
Isle; автоподбор возьмёт их сам, как только по ним пойдёт торговля.

ПОЧЕМУ ПРИ ЭТОМ ЕСТЬ РУЧНЫЕ ПИНЫ. Автоподбор смотрит на сегодняшнюю
ликвидность и по определению слеп к тому, что интересно лично Карену.
Пины ведутся руками и в отбор не участвуют — они просто всегда в списке.

ФИЛЬТР МУСОРА — НЕ ПРИДИРКА, А НЕОБХОДИМОСТЬ. Первый прогон рейтинга без
фильтра выдал в топе «Сломанный тяжелый метательный кинжал» по 325 тысяч
золота, «Разрывной патрон» по 550 тысяч и «Обломок доспехов». Это
ванильный хлам и квестовые предметы: их на весь EU висит по две сотни
штук, цена держится коллекционерами и к спекуляции отношения не имеет.
Отсекаются они не ценой, а ГЛУБИНОЙ рынка (сколько единиц и в скольких
лотах) плюс видом предмета.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import auction, config, items

PATH = config.DATA_DIR / "watchlist.json"

# Сколько предметов держим в слежке. 80 × 24 снимка × 30 дней ≈ 58 тысяч
# строк в месяц, около 3 МБ CSV — сопоставимо с историей жетона и вполне
# по силам git.
SIZE = 80

# Сколько кандидатов разбирать: справочник заполняется по два запроса на
# предмет, поэтому глубже топ-350 лезть незачем — ниже начинается шум.
CANDIDATES = 350

# Пороги глубины. Ниже них товар не торгуется, а коллекционируется.
MIN_QUANTITY = 1_000
MIN_LOTS = 50

# Виды предметов, которые не берём никогда (числовые ID, не названия).
EXCLUDED_CLASSES = {
    12,  # Задание — квестовый хлам вроде «Обломка доспехов»
    16,  # Символы — мёртвая механика
}
# Отдельно «Разное / Хлам»: класс 15 сам по себе полезен (там сидят
# реагенты вроде «Целого зуба»), а вот подкласс 0 — это именно мусор.
EXCLUDED_SUBCLASSES = {(15, 0)}

# Ручные пины Карена. В отбор не участвуют, живут в списке всегда.
PINNED: tuple[int, ...] = (
    253307,  # Зачарованный гелиотроп — эпический самоцвет ювелира Midnight
)


def _is_tradeable(entry: dict) -> bool:
    class_id = entry.get("class_id")
    subclass_id = entry.get("subclass_id")
    if class_id in EXCLUDED_CLASSES:
        return False
    return (class_id, subclass_id) not in EXCLUDED_SUBCLASSES


def load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def item_ids() -> list[int]:
    """ID под слежкой. Пины добавляются всегда, даже если список пуст."""
    stored = load().get("items") or []
    merged = list(dict.fromkeys([*stored, *PINNED]))
    return [int(i) for i in merged]


def refresh(token: str, *, region: str = config.PRIMARY_REGION, verbose: bool = True) -> dict:
    """Пересобрать список по свежему снимку. Запускается редко, вручную.

    Порядок важен: сначала дешёвая сортировка по глубине (она не требует
    справочника), потом справочник ТОЛЬКО для верхушки, и лишь затем отсев
    по виду предмета. Наоборот было бы 12 тысяч запросов на ровном месте.
    """
    snapshot = auction.fetch(region, token)
    if verbose:
        print(
            f"[KMARKET] Снимок {region.upper()}: {len(snapshot.quotes)} предметов, "
            f"время Blizzard {snapshot.updated:%Y-%m-%d %H:%M} UTC",
            flush=True,
        )

    deep = [
        quote
        for quote in snapshot.quotes.values()
        if quote.quantity >= MIN_QUANTITY and quote.lots >= MIN_LOTS
    ]
    deep.sort(key=lambda q: q.depth_gold, reverse=True)
    candidates = [q.item_id for q in deep[:CANDIDATES]]

    if verbose:
        print(
            f"[KMARKET] Прошли порог глубины: {len(deep)}; "
            f"разбираю верхние {len(candidates)}",
            flush=True,
        )
    known = items.resolve([*candidates, *PINNED], token, region=region, progress=verbose)

    chosen: list[int] = []
    for item_id in candidates:
        entry = known.get(str(item_id))
        if entry and _is_tradeable(entry):
            chosen.append(item_id)
        if len(chosen) >= SIZE:
            break

    merged = list(dict.fromkeys([*PINNED, *chosen]))
    payload = {
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "region": region,
        "size": len(merged),
        "pinned": list(PINNED),
        "items": merged,
    }
    PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(PATH)

    if verbose:
        print(f"[KMARKET] В слежке {len(merged)} предметов:", flush=True)
        for item_id in merged:
            quote = snapshot.quotes.get(item_id)
            depth = f"{quote.depth_gold / 1e6:.1f} млн з" if quote else "нет в снимке"
            pin = " ПИН" if item_id in PINNED else ""
            print(f"    {item_id:>7}  {items.name_of(item_id):<44.44} {depth}{pin}")
    return payload


def main(argv: list[str] | None = None) -> int:
    """python -m kmarket.watchlist — пересобрать список слежки."""
    from .blizzard import get_access_token

    try:
        refresh(get_access_token())
    except Exception as error:  # noqa: BLE001 — наружу нужен текст, не стек
        print(f"[KMARKET] Не удалось обновить список: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
