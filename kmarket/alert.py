"""Правила отправки Telegram-алертов.

ГДЕ ЭТО КРУТИТСЯ. Отдельный workflow (.github/workflows/alert.yml), а не
сборщик: вердикт требует pandas, а сборщик обязан оставаться без
зависимостей. Алерт-задача ставит pandas, читает свежую историю (её уже
закоммитил сборщик) и решает, слать ли пуш.

КАК НЕ СПАМИТЬ. Дедуп идёт ПО СОСТОЯНИЮ, а не по расписанию: alert.yml
может запускаться хоть каждый час, но пуш уходит только когда что-то
РЕАЛЬНО изменилось против запомненного в data/alert_state.json. Поэтому
частота крона на объём сообщений не влияет.

ТОЛЬКО EU. US мы собираем как гипотезу об опережающем индикаторе, а не
как то, что Карен покупает — слать по нему алерты значит шуметь. Регион
алертов = config.PRIMARY_REGION.

ЧТО ДОСТОЙНО ПУША (для покупателя, копящего впрок):
  1. Вердикт открыл окно «БРАТЬ» — или закрыл его.
  2. Глубокое дно (нижние 10% за 90 дней) — отдельный, более сильный пинг.
  3. Впереди игровое событие — цена исторически задрана перед ним и падает
     после; предупреждаем один раз на событие.

АУКЦИОННЫХ АЛЕРТОВ НЕТ. Сливы дешёвых лотов будили человека, пока
выкуп хвоста считался стратегией; дважды проверенный золотом, он оказался
неисполнимым (снимок Blizzard часовой, хвост разбирают за минуты), и ветка
закрыта вместе с её алертами.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import blizzard, config, gameinfo, notify
from .analytics import events, report as build_report

STATE_FILE = config.DATA_DIR / "alert_state.json"

DEEP_BOTTOM_ENTER = 10.0  # входим в режим «глубокое дно»
DEEP_BOTTOM_EXIT = 15.0   # выходим (гистерезис, чтобы не мигать у порога)
EVENT_HORIZON_DAYS = 21   # за сколько дней предупреждать о событии

EMOJI = {"buy": "🟢", "wait": "🟡", "avoid": "🔴"}


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _price_line(report: dict) -> str:
    current = report["current"]
    local = datetime.fromisoformat(current["updated_local"])
    return f"<b>{current['price']:,} g</b>".replace(",", " ") + f"  ·  {local:%d.%m %H:%M}"


def _percentile_90(report: dict) -> float | None:
    for window in report["windows"]:
        if window["days"] == 90:
            return window["percentile"]
    return None


def _verdict_message(report: dict, opened: bool) -> str:
    verdict = report["verdict"]
    head = "🟢 Открылось окно покупки" if opened else "Окно покупки закрылось"
    lines = [f"<b>{head}</b>", "", f"{EMOJI.get(verdict['state'], '')} {verdict['title']} — {verdict['summary']}", _price_line(report)]
    if verdict["reasons"]:
        lines += ["", verdict["reasons"][0]]
    if opened:
        stock = report["backtest"].get("stockpile")
        if stock:
            lines += [
                "",
                f"Правило вердикта на истории экономит {stock['saving_pct']}% "
                f"(~{stock['saving_gold']:,} g на жетон).".replace(",", " "),
            ]
    return "\n".join(lines)


def _deep_bottom_message(report: dict) -> str:
    pct = _percentile_90(report)
    return "\n".join(
        [
            "🔻 <b>Глубокое дно</b>",
            "",
            f"Цена в нижних {pct:.0f}% за 90 дней — дешевле почти не бывает.",
            _price_line(report),
            "",
            "Для запаса впрок это лучшие входы: по бэктесту жадничать и ждать "
            "ещё глубже невыгодно.",
        ]
    )


def _event_message(event: dict) -> str:
    kind_word = {"launch": "запуском дополнения", "season": "стартом сезона", "prepatch": "пре-патчем"}
    before = "+12%" if event["kind"] == "launch" else "+7%"
    after = "−12%" if event["kind"] == "launch" else "−5%"
    return "\n".join(
        [
            f"⏳ <b>Через {event['in_days']} дн — {event['label']}</b>",
            "",
            f"Перед {kind_word.get(event['kind'], 'событием')} цена исторически "
            f"задрана ({before} за месяц до) и падает после ({after}).",
            "",
            "Если запас нужен К контенту — брать сильно заранее. Если ждать "
            "можешь — выгоднее переждать обвал после старта.",
        ]
    )


def _stale_message(stale: dict) -> str:
    """Напоминание, что календарь событий опустел.

    Без него список молча протухает, а вердикт продолжает уверенно
    ссылаться на давно прошедшие патчи. Пусть лучше попросит.
    """
    if stale["nearest"]:
        tail = (
            f"Ближайшее известное — {stale['nearest']['label']} "
            f"({stale['nearest']['date']}), это дальше {stale['horizon_days']} дней."
        )
    else:
        tail = "Будущих дат я не знаю ни одной."
    return "\n".join(
        [
            "🗓 <b>Календарь событий пора пополнить</b>",
            "",
            tail,
            "",
            "Выход патчей и старты сезонов система замечает сама, но только "
            "ПОСЛЕ факта. Предупредить заранее — а это самый сильный сигнал, "
            "+12% за месяц до запуска — можно лишь по объявленным датам.",
            "",
            "Если Blizzard уже что-то анонсировала, скажи Claude — он внесёт.",
        ]
    )


def _game_change_message(found: list[dict]) -> str:
    lines = ["🎮 <b>В игре что-то поменялось</b>", ""]
    for item in found:
        lines.append(f"· {item['label']} — {item['date']} ({item['source']})")
    lines += [
        "",
        "Событие добавлено в календарь автоматически. С этого момента "
        "вердикт считает, что мы в фазе ПОСЛЕ: цена обычно оседает.",
    ]
    return "\n".join(lines)


def evaluate(region: str, report: dict, prior: dict) -> tuple[list[str], dict]:
    """Сравнить свежий отчёт с запомненным состоянием. Вернуть (сообщения, новое состояние)."""
    messages: list[str] = []
    state = dict(prior)

    verdict_state = report["verdict"]["state"]
    was = prior.get("verdict")
    # Пуш на смене окна покупки: открылось (стало buy) или закрылось (было buy).
    if was != verdict_state and "buy" in (was, verdict_state):
        messages.append(_verdict_message(report, opened=(verdict_state == "buy")))
    state["verdict"] = verdict_state

    # Глубокое дно с гистерезисом.
    pct = _percentile_90(report)
    deep = bool(prior.get("deep_bottom"))
    if pct is not None:
        if not deep and pct <= DEEP_BOTTOM_ENTER:
            messages.append(_deep_bottom_message(report))
            deep = True
        elif deep and pct > DEEP_BOTTOM_EXIT:
            deep = False
    state["deep_bottom"] = deep

    # Приближающиеся события — один раз на событие.
    notified = list(prior.get("events_notified", []))
    upcoming_labels = {e["label"] for e in events.upcoming(5)}
    for event in events.upcoming(3):
        if event["in_days"] <= EVENT_HORIZON_DAYS and event["label"] not in notified:
            messages.append(_event_message(event))
            notified.append(event["label"])
    # Забываем прошедшие события, чтобы список не рос и повтор сработал в след. цикле.
    state["events_notified"] = [label for label in notified if label in upcoming_labels]

    state.pop("auction_seen", None)  # след закрытой ветки сливов

    # Список будущих событий пуст — самая дорогая из молчаливых поломок.
    # Напоминаем не чаще раза в неделю, иначе это станет шумом.
    stale = events.staleness()
    if stale["stale"]:
        last = prior.get("stale_reminded")
        today = datetime.now(timezone.utc).date().isoformat()
        if not last or (
            datetime.fromisoformat(today) - datetime.fromisoformat(last)
        ).days >= 7:
            messages.append(_stale_message(stale))
            state["stale_reminded"] = today
    else:
        state.pop("stale_reminded", None)

    return messages, state


def _watch_game(dry_run: bool) -> list[str]:
    """Заметить, что вышел патч или сменился сезон.

    Живёт здесь, а не в сборщике: сборщику нельзя лишних запросов и
    лишних файлов состояния, а алерт и так ходит в сеть раз в час.
    """
    try:
        found, state = gameinfo.check(blizzard.get_access_token())
    except Exception as error:  # noqa: BLE001 — не повод ронять остальные алерты
        print(f"[KMARKET] Проверка версии игры не удалась: {error}")
        return []
    if not dry_run:
        gameinfo.save_state(state)
    if not found:
        return []
    for item in found:
        print(f"[KMARKET] Обнаружено: {item['label']} — {item['date']}")
    return [_game_change_message(found)]


def run(*, dry_run: bool = False) -> int:
    region = config.PRIMARY_REGION
    # Первым делом: не вышел ли патч. Если вышел, вердикт ниже должен
    # считаться уже с учётом нового события, а не со старым календарём.
    game_messages = _watch_game(dry_run)

    report = build_report(region, fresh=True)
    if report.get("empty"):
        print("[KMARKET] Истории нет — алерты пропущены.")
        return 0

    all_state = _load_state()
    messages, new_state = evaluate(region, report, all_state.get(region, {}))
    messages = [*game_messages, *messages]

    if not messages:
        print(f"[KMARKET] {region.upper()}: изменений нет, пуш не нужен.")
    for text in messages:
        if dry_run:
            print("---\n" + text.replace("<b>", "").replace("</b>", ""))
        else:
            ok = notify.send(text)
            print(f"[KMARKET] Алерт {'отправлен' if ok else 'НЕ ушёл'}.")

    if not dry_run:
        all_state[region] = new_state
        all_state["_updated"] = datetime.now(timezone.utc).isoformat()
        _save_state(all_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(dry_run="--dry-run" in sys.argv))
