"""Справочник предметов: имя, вид, качество, иконка и ранг ремесла.

Ноль зависимостей. Кэш лежит в `data/items.json` и коммитится: это
справочные данные, они почти не меняются, а без них аукционные CSV — это
голые числовые ID, нечитаемые глазами.

ЗАЧЕМ ВООБЩЕ РАНГ. Реагенты начиная с Dragonflight выпускаются в
нескольких качествах ремесла, и Blizzard даёт им РАЗНЫЕ ID при ПОЛНОСТЬЮ
СОВПАДАЮЩИХ карточках. Проверено 2026-08-06 на 242620 и 242621: одинаковы
имя, качество, описание, даже файл иконки. В списке слежки они выглядели
бы двумя неразличимыми строками «Мерцающая самоцветная пыль», хотя это
разный товар с разной ценой (у Азефита ранг 1 стоит 14 з, ранг 2 — 47 з).

КАК РАНГ ВЫВОДИТСЯ. Первая зацепка — поле `modified_crafting`: у ранговых
предметов оно есть и содержит `category.id`, общий для всей группы.
Проверено, что группы настоящие: в 125 из 126 все участники носят ОДНО
имя, то есть это именно ранги одного предмета, а не разные предметы,
подходящие в один слот рецепта.

А вот ПОРЯДОК внутри группы — место, где легко ошибиться, и я ошибся
(2026-08-07). Сначала ранг считался просто позицией по возрастанию ID.
Для трав это верно (Азефит 236774 стоит 14 з, 236775 — 47 з), но для
расходников порядок ОБРАТНЫЙ, и это доказывает уровень предмета:

    241326  Настой Расколотого Солнца  уровень 295
    241327  Настой Расколотого Солнца  уровень 278

Меньший ID оказался старшим рангом. Поэтому сортируем по УРОВНЮ, а при
равных уровнях — по ID.

ЧЕСТНАЯ ГРАНИЦА ЭТОГО МЕТОДА. Уровень различается только у 22 групп из
125, цена продажи торговцу — у четырёх. Для остальной сотни реагентов
API не даёт НИЧЕГО: имя, качество, описание и даже файл иконки у рангов
совпадают (проверено на 242620 и 242621). Там порядок по ID — это
общепринятая среди трекеров догадка, а не факт, и подтвердить её нечем.

ПО ЦЕНЕ ПРОВЕРЯТЬ НЕЛЬЗЯ — это выглядит соблазнительно и неверно. К
середине дополнения сбор прокачан у всех, почти всё добываемое идёт
высшим рангом, и НИЗШИЙ становится дефицитным. Поэтому «ранг 1 дороже
ранга 2» — обычное явление рынка, а не признак перепутанного порядка.

ГРАБЛЯ, ИЗ-ЗА КОТОРОЙ ЕСТЬ ПРОБА СОСЕДЕЙ. Группу нельзя собирать только
из тех предметов, что попали в наш список: если в топ ликвидности вышел
только ранг 2, он окажется единственным в группе и получит подпись
«ранг 1 из 1» — тихо неверную. Поэтому вокруг каждого рангового предмета
пробуются соседние ID (±NEIGHBOURS), и в группу берутся все, у кого
совпал `category.id`. У Midnight рангов оказалось два, не три, — ещё одна
причина не зашивать количество числом.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

from . import blizzard, config

CACHE_PATH = config.DATA_DIR / "items.json"
# Насколько далеко от рангового предмета искать его собратьев по группе.
# Ранги идут подряд, но между группами попадаются посторонние ID, поэтому
# запас есть, а лишние пробы дёшевы и кэшируются.
NEIGHBOURS = 3

_cache: dict[str, dict] | None = None


def load() -> dict[str, dict]:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def save() -> None:
    data = load()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = CACHE_PATH.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    temp.replace(CACHE_PATH)  # атомарно, как и месячные CSV


def get(item_id: int) -> dict | None:
    return load().get(str(item_id))


def name_of(item_id: int) -> str:
    """Имя с рангом, готовое к показу. Незнакомый предмет — честно по ID."""
    entry = get(item_id)
    if not entry:
        return f"Предмет {item_id}"
    name = entry.get("name") or f"Предмет {item_id}"
    rank, rank_of = entry.get("rank"), entry.get("rank_of")
    if rank and rank_of and rank_of > 1:
        return f"{name} · ранг {rank}"
    return name


def _fetch_raw(item_id: int, token: str, region: str, locale: str) -> dict | None:
    url = (
        f"https://{region}.api.blizzard.com/data/wow/item/{item_id}"
        f"?namespace=static-{region}&locale={locale}"
    )
    try:
        return blizzard._http_json(url, headers={"Authorization": f"Bearer {token}"})
    except RuntimeError as error:
        # 404 — обычное дело: пробуя соседей, мы намеренно стучимся в
        # несуществующие ID. Отличать их от настоящих сбоев незачем,
        # результат один — предмета нет.
        if "404" in str(error):
            return None
        raise
    except urllib.error.HTTPError:
        return None


def _icon(item_id: int, token: str, region: str) -> str:
    url = (
        f"https://{region}.api.blizzard.com/data/wow/media/item/{item_id}"
        f"?namespace=static-{region}"
    )
    try:
        media = blizzard._http_json(url, headers={"Authorization": f"Bearer {token}"})
    except (RuntimeError, urllib.error.HTTPError):
        return ""
    for asset in media.get("assets", ()):
        if asset.get("key") == "icon" or asset.get("value"):
            return asset.get("value", "")
    return ""


def _entry(item_id: int, raw: dict, token: str, region: str) -> dict:
    crafting = raw.get("modified_crafting") or {}
    category = (crafting.get("category") or {}).get("id")
    return {
        "id": item_id,
        "name": raw.get("name") or f"Предмет {item_id}",
        "class": (raw.get("item_class") or {}).get("name", ""),
        "subclass": (raw.get("item_subclass") or {}).get("name", ""),
        # Числовые ID вида — по ним фильтруется мусор. По русским названиям
        # («Хлам», «Задание») фильтровать нельзя: они зависят от locale, и
        # смена языка молча выключила бы отсев.
        "class_id": (raw.get("item_class") or {}).get("id"),
        "subclass_id": (raw.get("item_subclass") or {}).get("id"),
        "quality": (raw.get("quality") or {}).get("name", ""),
        # Уровень предмета — единственное, чем API вообще различает ранги,
        # и то лишь у части групп. Порядок рангов строится по нему.
        "level": raw.get("level"),
        "icon": _icon(item_id, token, region),
        "category": category,
        "rank": None,  # проставляется в _rank_groups, когда группа собрана
        "rank_of": None,
    }


def _rank_groups(token: str, region: str, locale: str) -> None:
    """Достроить группы качества и проставить ранги всем известным предметам."""
    data = load()

    # Шаг 1 — найти собратьев за пределами уже известного набора.
    tiered = [e for e in data.values() if e.get("category")]
    for entry in list(tiered):
        item_id = entry["id"]
        for neighbour in range(item_id - NEIGHBOURS, item_id + NEIGHBOURS + 1):
            if neighbour == item_id or str(neighbour) in data:
                continue
            raw = _fetch_raw(neighbour, token, region, locale)
            if not raw:
                continue
            candidate = _entry(neighbour, raw, token, region)
            # Берём только настоящих собратьев: тот же id категории.
            if candidate["category"] == entry["category"]:
                data[str(neighbour)] = candidate

    # Шаг 2 — ранг это позиция в группе, отсортированной по УРОВНЮ, а при
    # равных уровнях по ID. Один только ID врёт: у расходников порядок
    # обратный (см. шапку модуля про Настой Расколотого Солнца).
    groups: dict[int, list[dict]] = {}
    for entry in data.values():
        if entry.get("category"):
            groups.setdefault(entry["category"], []).append(entry)
    for category, members in groups.items():
        members.sort(key=lambda e: (e.get("level") or 0, e["id"]))
        for position, entry in enumerate(members, start=1):
            data[str(entry["id"])]["rank"] = position
            data[str(entry["id"])]["rank_of"] = len(members)


def resolve(
    item_ids: list[int],
    token: str,
    *,
    region: str = config.PRIMARY_REGION,
    locale: str = "ru_RU",
    progress: bool = False,
) -> dict[str, dict]:
    """Дозаполнить справочник по списку ID и пересчитать ранги.

    Медленно (по два запроса на новый предмет), поэтому вызывается только
    при обновлении списка слежки, а не на каждом часовом сборе.
    """
    data = load()
    missing = [i for i in item_ids if str(i) not in data]
    for done, item_id in enumerate(missing, start=1):
        raw = _fetch_raw(item_id, token, region, locale)
        if raw:
            data[str(item_id)] = _entry(item_id, raw, token, region)
        # Сохраняем по ходу. Полный проход — это тысячи запросов и минуты
        # работы; один раз он уже был потерян целиком из-за остановки на
        # середине, и переделывать всё с нуля глупо, когда файл кэша
        # и так пишется атомарно.
        if done % 50 == 0:
            save()
        if progress and done % 25 == 0:
            print(f"[KMARKET] Справочник: {done} из {len(missing)}", flush=True)

    _rank_groups(token, region, locale)
    save()
    return data
