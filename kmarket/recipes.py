"""Рецепты профессий: что нужно для крафта в текущем дополнении.

Ноль зависимостей — как и всё, что может понадобиться сборщику.

    python -m kmarket.recipes            # собрать и записать data/recipes.json

ЗАЧЕМ ЭТОТ МОДУЛЬ ПОЯВИЛСЯ (решение Карена 2026-08-09). Список слежки
набирался по ЛИКВИДНОСТИ: топ товаров по обороту. Это отвечало на вопрос
«где много торгуют», а нужен ответ на другой — «что людям НУЖНО».

Разница принципиальная. Оборот большой у всего подряд, включая старьё,
которое перекупщики гоняют друг другу. А спрос на реагент создаёт игра:
вышел патч — все побежали крафтить снаряжение, и то, что входит в
рецепты, разбирают. Кончился сезон — тот же реагент не нужен никому.
Карен сформулировал это на «Зачарованном гелиотропе»: к старту сезона он
нужен всем сразу, в конце — никому.

ПОЧЕМУ ЭТО МОЖНО ВЗЯТЬ ИЗ ДАННЫХ, А НЕ УГАДЫВАТЬ. У рецепта в API есть
поле `reagents`, и там лежат id предметов с количеством. Значит список
«что нужно для крафта в Midnight» — не мнение, а выгрузка.

ЧЕГО В API НЕТ. Поля `crafted_item` у рецептов текущего дополнения нет
ни у одного из проверенных (52 рецепта алхимии Midnight — ноль). То есть
ЧТО получается на выходе, API не говорит, и связать сырьё с продуктом
можно только сопоставлением по названию. Поэтому здесь собирается
надёжная половина — сырьё, — а продукты отмечаются как «имя рецепта» и
сопоставляются отдельно.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
from pathlib import Path

from . import config
from .blizzard import get_access_token, _http_json

# По какому слову в названии тира узнаём текущее дополнение. Тир называется
# «Алхимия Midnight», «Кузнечное дело Midnight» и так далее.
#
# ПОЧЕМУ НЕ «ПОСЛЕДНИЙ ТИР В СПИСКЕ». Порядок в ответе не обещан, а
# ошибиться тут дорого: список слежки уедет на прошлое дополнение целиком
# и молча. Слово в названии проверяемо глазами и ломается громко —
# профессий найдётся ноль, и сбор откажется писать файл.
CURRENT_TIER_MARK = "Midnight"

# Пауза между запросами. Рецептов около семисот, и гнать их залпом незачем:
# модуль разовый, лишняя минута ничего не стоит, а вежливость к чужому API
# стоит того, чтобы нас не начали ограничивать.
PAUSE = 0.05

RECIPES_FILE = config.DATA_DIR / "recipes.json"


def _get(path: str, token: str) -> dict:
    url = f"https://{config.PRIMARY_REGION}.api.blizzard.com{path}"
    url += f"?namespace=static-{config.PRIMARY_REGION}&locale=ru_RU"
    return _http_json(url, headers={"Authorization": f"Bearer {token}"})


def current_tiers(token: str) -> list[tuple[int, str, int, str]]:
    """Профессии текущего дополнения: (id профессии, имя, id тира, имя тира)."""
    index = _get("/data/wow/profession/index", token)
    found: list[tuple[int, str, int, str]] = []
    for profession in index.get("professions", []):
        try:
            info = _get(f"/data/wow/profession/{profession['id']}", token)
        except Exception as error:  # noqa: BLE001 — одна профессия не повод падать
            print(f"[KMARKET] {profession.get('name')}: {error}", file=sys.stderr)
            continue
        for tier in info.get("skill_tiers", []):
            if CURRENT_TIER_MARK in tier.get("name", ""):
                found.append(
                    (profession["id"], profession["name"], tier["id"], tier["name"])
                )
        time.sleep(PAUSE)
    return found


def collect(token: str) -> dict:
    """Все реагенты текущего дополнения с весами.

    Вес предмета — в скольких рецептах он встречается и сколько единиц
    суммарно требует. ОБА числа нужны, и вот почему: руда входит в один
    рецепт по двести штук, а редкая добавка — в двадцать рецептов по одной.
    Первую будут покупать мешками, вторую — часто и понемногу. Судить об
    их важности одним числом значит потерять половину картины.
    """
    tiers = current_tiers(token)
    if not tiers:
        raise RuntimeError(
            f"Не найдено ни одной профессии с тиром «{CURRENT_TIER_MARK}». "
            f"Похоже, вышло новое дополнение — поправь CURRENT_TIER_MARK."
        )

    reagents: dict[int, dict] = {}
    products: list[dict] = []
    seen_recipes = 0

    for prof_id, prof_name, tier_id, tier_name in tiers:
        try:
            detail = _get(
                f"/data/wow/profession/{prof_id}/skill-tier/{tier_id}", token
            )
        except Exception as error:  # noqa: BLE001
            print(f"[KMARKET] {tier_name}: {error}", file=sys.stderr)
            continue

        recipe_ids = [
            recipe["id"]
            for category in detail.get("categories", [])
            for recipe in category.get("recipes", [])
        ]
        for recipe_id in recipe_ids:
            try:
                recipe = _get(f"/data/wow/recipe/{recipe_id}", token)
            except Exception:  # noqa: BLE001 — битый рецепт пропускаем молча
                continue
            seen_recipes += 1
            items = recipe.get("reagents") or []
            if not items:
                continue  # служебный рецепт («Переделка снаряжения») — не крафт
            for entry in items:
                item = entry.get("reagent") or {}
                item_id = item.get("id")
                if not item_id:
                    continue
                row = reagents.setdefault(
                    item_id,
                    {
                        "name": item.get("name", ""),
                        "recipes": 0,
                        "total_qty": 0,
                        "professions": [],
                    },
                )
                row["recipes"] += 1
                row["total_qty"] += int(entry.get("quantity") or 0)
                if prof_name not in row["professions"]:
                    row["professions"].append(prof_name)
            products.append(
                {
                    "recipe_id": recipe_id,
                    "name": recipe.get("name", ""),
                    "profession": prof_name,
                    # СКОЛЬКО СЛОТОВ КАЧЕСТВА У РЕЦЕПТА. Туда игрок кладёт
                    # реагенты, которых в `reagents` нет: слот задаётся
                    # КАТЕГОРИЕЙ, а категория отдаёт только имя, без состава.
                    #
                    # Для маржи это решающее. Себестоимость по одним
                    # фиксированным реагентам ЗАНИЖЕНА, значит выгода
                    # завышена — а по завышенной выгоде тратят золото.
                    # Поэтому число слотов едет в файл и в интерфейс, и
                    # рецепт со слотами честно помечается как «неполная
                    # себестоимость», а не выдаёт себя за посчитанный.
                    "quality_slots": len(recipe.get("modified_crafting_slots") or []),
                    # ИМЕНА СЛОТОВ — ЭТО ИМЕНА РЕАГЕНТОВ. Считать по ним
                    # себестоимость нельзя (количества нет нигде), но для
                    # ответа «что нужно дополнению» они бесценны: 97%
                    # рецептов кладут основное сырьё именно сюда, и в
                    # `reagents` его нет вовсе.
                    "slots": [
                        (slot.get("slot_type") or {}).get("name", "")
                        for slot in (recipe.get("modified_crafting_slots") or [])
                        if (slot.get("slot_type") or {}).get("name")
                    ],
                    # СОСТАВ ИМЕННО ЭТОГО РЕЦЕПТА. Сводка по реагентам выше
                    # отвечает на вопрос «что вообще нужно дополнению», а
                    # для себестоимости нужно знать, сколько чего идёт в
                    # КОНКРЕТНОЕ изделие. Без этого крафт-маржу не посчитать.
                    "items": [
                        {
                            "id": (part.get("reagent") or {}).get("id"),
                            "name": (part.get("reagent") or {}).get("name", ""),
                            "quantity": int(part.get("quantity") or 0),
                        }
                        for part in items
                        if (part.get("reagent") or {}).get("id")
                    ],
                }
            )
            time.sleep(PAUSE)
        print(
            f"[KMARKET] {tier_name}: рецептов {len(recipe_ids)}, "
            f"реагентов накоплено {len(reagents)}",
            flush=True,
        )

    return {
        "mark": CURRENT_TIER_MARK,
        "professions": [
            {"id": p, "name": n, "tier_id": t, "tier": tn} for p, n, t, tn in tiers
        ],
        "recipes_seen": seen_recipes,
        "reagents": {str(k): v for k, v in reagents.items()},
        "products": products,
    }


def _find_item(name: str, token: str) -> int | None:
    """id предмета по ТОЧНОМУ имени. None — если такого нет.

    Поиск Blizzard нечёткий: по запросу «Зачарованный гелиотроп» он выдаёт
    сотню предметов, начиная с «Зачарованного агата». Поэтому берём только
    ТОЧНОЕ совпадение имени, а не первый результат, — иначе связь рецепта
    с продуктом будет тихо неверной, и маржа посчитается по чужой цене.
    """
    query = urllib.parse.quote(name)
    url = (
        f"https://{config.PRIMARY_REGION}.api.blizzard.com/data/wow/search/item"
        f"?namespace=static-{config.PRIMARY_REGION}&name.ru_RU={query}&_pageSize=100"
    )
    try:
        found = _http_json(url, headers={"Authorization": f"Bearer {token}"})
    except Exception:  # noqa: BLE001 — один промах не повод ронять проход
        return None
    wanted = name.strip().lower()
    for result in found.get("results", []):
        data = result.get("data") or {}
        if ((data.get("name") or {}).get("ru_RU") or "").strip().lower() == wanted:
            return data.get("id")
    return None


def link_products(token: str, data: dict | None = None) -> dict:
    """Найти, ЧТО получается по каждому рецепту, и дописать в справочник.

    ПОЧЕМУ ПОИСКОМ ПО ИМЕНИ, А НЕ ИЗ ОТВЕТА. У рецептов текущего
    дополнения поля `crafted_item` нет ни у одного из проверенных (52
    рецепта алхимии Midnight — ноль). Blizzard его просто не отдаёт, а
    связь сырья с продуктом нужна: без неё нельзя сказать, выгодно ли
    вообще крафтить.

    Имя рецепта совпадает с именем предмета либо целиком, либо после
    двоеточия («Трансмутация: частица дикой магии» → «Частица дикой
    магии»). Пробуем оба варианта.

    ЭТО СОПОСТАВЛЕНИЕ, А НЕ ВЫГРУЗКА, и относиться к нему надо
    соответственно: несовпавшие рецепты остаются без продукта, и это
    честнее, чем привязать похожее.
    """
    data = data or load()
    products = data.get("products") or []
    linked = 0
    for entry in products:
        if entry.get("product_id"):
            continue
        name = entry.get("name") or ""
        for candidate in (name, name.split(":")[-1].strip()):
            if not candidate:
                continue
            item_id = _find_item(candidate, token)
            if item_id:
                entry["product_id"] = item_id
                entry["product_name"] = candidate
                linked += 1
                break
        time.sleep(PAUSE)
    data["products_linked"] = sum(1 for p in products if p.get("product_id"))
    print(
        f"[KMARKET] Продукты: связано {linked} за этот проход, "
        f"всего {data['products_linked']} из {len(products)}",
        flush=True,
    )
    return data


def link_slots(token: str, data: dict | None = None) -> dict:
    """Превратить имена слотов качества в id предметов.

    ЗАЧЕМ. Основное сырьё 97% рецептов лежит именно в слотах, а в
    `reagents` его нет вовсе. Считать себестоимость по слотам нельзя —
    количества Blizzard не сообщает, — но ответить «что нужно дополнению»
    они помогают лучше всего остального.

    ЧАСТЬ ИМЁН — НЕ ПРЕДМЕТЫ, А ОПИСАНИЯ: «Наполняет силой», «Позволяет
    указать второстепенные характеристики», «Усиление». Отделять их
    вручную не нужно и нельзя (список меняется каждое дополнение) —
    достаточно того, что точный поиск по имени их просто не находит.
    Ищем точное совпадение, а не похожее: «Искра» иначе привяжется к
    первому попавшемуся предмету со словом «искра» в названии.
    """
    data = data or load()
    names: dict[str, int] = {}
    for entry in data.get("products") or []:
        for name in entry.get("slots") or []:
            names[name] = names.get(name, 0) + 1

    found: dict[str, dict] = dict(data.get("slot_reagents") or {})
    for name, uses in sorted(names.items(), key=lambda kv: -kv[1]):
        if name in found:
            continue
        item_id = _find_item(name, token)
        found[name] = {"id": item_id, "recipes": uses}
        time.sleep(PAUSE)

    data["slot_reagents"] = found
    real = sum(1 for v in found.values() if v.get("id"))
    print(
        f"[KMARKET] Слоты: имён {len(found)}, из них настоящих предметов {real} "
        f"(остальное — описания слотов, а не реагенты)",
        flush=True,
    )
    return data


def save(data: dict) -> None:
    RECIPES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = RECIPES_FILE.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    temp.replace(RECIPES_FILE)


def load() -> dict:
    if not RECIPES_FILE.exists():
        return {}
    try:
        return json.loads(RECIPES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def reagent_ids() -> list[int]:
    """id всего сырья текущего дополнения — вход для списка слежки.

    ДВА ИСТОЧНИКА, И ВТОРОЙ ВАЖНЕЕ. `reagents` — фиксированная часть
    состава, её отдаёт сам рецепт. `slot_reagents` — то, что кладут в
    слоты качества, и именно там лежит ОСНОВНОЕ сырьё: слоты есть у 566
    рецептов из 586, а в фиксированной части у них бывает одна позиция.

    Считать себестоимость по слотам всё равно нельзя (количества API не
    сообщает), но для вопроса «за чем следить перед патчем» разницы нет:
    следить надо за тем, что будут покупать.
    """
    data = load()
    ids = {int(k) for k in (data.get("reagents") or {})}
    for entry in (data.get("slot_reagents") or {}).values():
        if entry.get("id"):
            ids.add(int(entry["id"]))
    return sorted(ids)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Рецепты профессий текущего дополнения")
    parser.add_argument(
        "--link-only",
        action="store_true",
        help="не пересобирать рецепты, только найти продукты для уже собранных",
    )
    args = parser.parse_args(argv)

    try:
        token = get_access_token()
    except Exception as error:  # noqa: BLE001
        print(f"[KMARKET] Не удалось получить токен: {error}", file=sys.stderr)
        return 1

    if args.link_only:
        data = load()
        if not data:
            print("[KMARKET] Сначала собери рецепты без --link-only", file=sys.stderr)
            return 1
        data = link_products(token, data)
        data = link_slots(token, data)
        save(data)
        return 0

    previous = load()
    try:
        data = collect(token)
    except Exception as error:  # noqa: BLE001
        print(f"[KMARKET] Сбор рецептов не удался: {error}", file=sys.stderr)
        return 1

    # Найденные ранее продукты переносим: их поиск стоит шестисот запросов,
    # и терять его из-за пересбора рецептов незачем — связь «рецепт →
    # предмет» от состава реагентов не зависит.
    known = {
        p["recipe_id"]: p
        for p in (previous.get("products") or [])
        if p.get("product_id")
    }
    carried = 0
    for entry in data.get("products") or []:
        old = known.get(entry["recipe_id"])
        if old:
            entry["product_id"] = old["product_id"]
            entry["product_name"] = old.get("product_name")
            carried += 1
    data["products_linked"] = carried
    if carried:
        print(f"[KMARKET] Перенесено связей рецепт→предмет: {carried}", flush=True)

    save(data)
    print(
        f"[KMARKET] Готово: профессий {len(data['professions'])}, "
        f"рецептов {data['recipes_seen']}, реагентов {len(data['reagents'])} "
        f"→ {RECIPES_FILE}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
