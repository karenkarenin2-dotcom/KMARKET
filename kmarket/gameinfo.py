"""Что происходит в самой игре: версия сборки и номера сезонов.

Ноль зависимостей.

ЗАЧЕМ ЭТОТ МОДУЛЬ. Даты патчей — самый сильный сигнал, который мы нашли
(размах 30% вокруг запуска дополнения против 11% у перцентилей). При этом
они вносились в `events.EVENTS` РУКАМИ, и в проектных заметках это честно
значилось как мина: список молча протухает, а вердикт продолжает делать
уверенное лицо на устаревших датах.

Оказалось, что часть работы API берёт на себя — просто не там, где её
принято искать.

ЧТО МОЖНО УЗНАТЬ АВТОМАТИЧЕСКИ:

1. **Версия сборки игры.** Она зашита в `namespace` внутри ссылок
   статических данных: `static-12.0.7_67808-eu`. Когда выходит патч,
   мажорная часть меняется (12.0.7 → 12.1.0). Отдельного эндпоинта
   «текущая версия» у Blizzard нет, но эта строка есть в любом ответе
   со ссылками, и она авторитетна.

2. **Старт сезона Мифик+** — `/data/wow/mythic-keystone/season/{id}`
   отдаёт `start_timestamp`. Проверено: сезон 17 начался 2026-03-18
   04:00 UTC, тогда как в ручном списке стояло 17 марта. API точнее.

3. **Номер сезона PvP** — `/data/wow/pvp-season/index`.

ЧЕГО УЗНАТЬ НЕЛЬЗЯ, И ЭТО ВАЖНО. Всё перечисленное — обнаружение ПОСТ
ФАКТУМ. API говорит «патч уже вышел», а не «выйдет через неделю».
Анонсы Blizzard живут в новостях, а не в данных. Поэтому:

* фаза ПОСЛЕ события (цена оседает) теперь ловится сама;
* фаза ДО события (цена задрана, самый ценный сигнал) по-прежнему
  требует, чтобы дату внесли руками.

Отсюда третья задача модуля — следить за тем, что будущие даты вообще
есть, и вовремя напоминать. Молчаливое протухание списка мы этим не
чиним полностью, но превращаем в громкое.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import blizzard, config

STATE_FILE = config.DATA_DIR / "game_state.json"

# Версия сборки прячется в namespace любой статической ссылки. Берём
# заведомо существующий предмет — он дешевле индекса и не меняется.
PROBE_ITEM = 236771  # Кровотерн, реагент Midnight
BUILD_RE = re.compile(r"static-([0-9.]+)_(\d+)-")


def _get(path: str, token: str) -> dict:
    return blizzard._http_json(
        f"https://{config.PRIMARY_REGION}.api.blizzard.com{path}",
        headers={"Authorization": f"Bearer {token}"},
    )


def _build_version(token: str) -> str | None:
    """Версия сборки вида «12.0.7» — по namespace внутри ответа."""
    data = _get(
        f"/data/wow/item/{PROBE_ITEM}?namespace=static-{config.PRIMARY_REGION}", token
    )
    blob = json.dumps(data)
    match = BUILD_RE.search(blob)
    return match.group(1) if match else None


def _season(kind: str, token: str) -> dict | None:
    """Текущий сезон и, если отдают, момент его старта."""
    try:
        index = _get(f"/data/wow/{kind}/index?namespace=dynamic-{config.PRIMARY_REGION}", token)
    except Exception:  # noqa: BLE001 — отсутствие сезона не повод падать
        return None
    current = (index or {}).get("current_season") or {}
    season_id = current.get("id")
    if season_id is None:
        return None

    started = None
    try:
        detail = _get(
            f"/data/wow/{kind}/{season_id}?namespace=dynamic-{config.PRIMARY_REGION}", token
        )
        stamp = detail.get("start_timestamp")
        if stamp:
            started = (
                datetime.fromtimestamp(stamp / 1000, tz=timezone.utc)
                .date()
                .isoformat()
            )
    except Exception:  # noqa: BLE001
        pass
    return {"id": season_id, "started": started}


def snapshot(token: str) -> dict:
    """Текущее состояние игры одним запросом-другим."""
    return {
        "build": _build_version(token),
        "mplus_season": _season("mythic-keystone/season", token),
        "pvp_season": _season("pvp-season", token),
        "checked_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(STATE_FILE)


def compare(fresh: dict, prior: dict) -> list[dict]:
    """Что изменилось в игре с прошлой проверки.

    Возвращает список событий вида
    {"kind": "content"|"season", "label": ..., "date": ...} —
    ровно в том виде, в каком их понимает analytics.events.
    """
    found: list[dict] = []

    # ПЕРВЫЙ ЗАПУСК НИЧЕГО НЕ ОБЪЯВЛЯЕТ. Пустого прошлого состояния
    # достаточно, чтобы принять текущую версию за «только что вышедшую»,
    # и сухой прогон честно поймал это: система сообщила «вышел патч
    # 12.0», хотя он вышел месяцы назад. Осторожно с пустой строкой —
    # `"".split(".")` возвращает `['']`, то есть НЕПУСТОЙ список.
    old_raw = (prior.get("build") or "").strip()
    new_raw = (fresh.get("build") or "").strip()
    old_build, new_build = old_raw.split("."), new_raw.split(".")
    # Сравниваем только мажор.минор: 12.0.7 → 12.0.8 это хотфикс, а
    # 12.0.x → 12.1.x — настоящий контентный патч.
    if old_raw and new_raw and len(new_build) >= 2 and new_build[:2] != old_build[:2]:
        found.append(
            {
                "kind": "content",
                "label": f"Патч {'.'.join(new_build[:2])}",
                "date": datetime.now(timezone.utc).date().isoformat(),
                "source": "обнаружено по версии сборки",
            }
        )

    for key, word in (("mplus_season", "Сезон Мифик+"), ("pvp_season", "Сезон PvP")):
        new = fresh.get(key) or {}
        old = prior.get(key) or {}
        if new.get("id") and old.get("id") and new["id"] != old["id"]:
            found.append(
                {
                    "kind": "season",
                    "label": f"{word} {new['id']}",
                    # Дата старта от Blizzard точнее нашей догадки; если
                    # её не отдали — датируем моментом обнаружения.
                    "date": new.get("started")
                    or datetime.now(timezone.utc).date().isoformat(),
                    "source": "обнаружено по номеру сезона",
                }
            )
    return found


def check(token: str) -> tuple[list[dict], dict]:
    """Сверить игру с запомненным состоянием. Возвращает (новые события, состояние)."""
    prior = load_state()
    fresh = snapshot(token)
    found = compare(fresh, prior)
    state = dict(fresh)
    # Обнаруженное копим в самом файле состояния: отдельного хранилища
    # ради десятка строк в год заводить незачем.
    state["detected"] = [*prior.get("detected", []), *found]
    return found, state
