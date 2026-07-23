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
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config, notify
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

    return messages, state


def run(*, dry_run: bool = False) -> int:
    region = config.PRIMARY_REGION
    report = build_report(region, fresh=True)
    if report.get("empty"):
        print("[KMARKET] Истории нет — алерты пропущены.")
        return 0

    all_state = _load_state()
    messages, new_state = evaluate(region, report, all_state.get(region, {}))

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
