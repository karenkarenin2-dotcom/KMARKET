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
  4. Слив на товарном аукционе: кто-то выставил дешёвый хвост, который
     можно выкупить и перевыставить.

ПОЧЕМУ АУКЦИОННЫЙ АЛЕРТ ВАЖНЕЕ ОСТАЛЬНЫХ. Окно покупки жетона живёт
днями, и часом раньше или позже — безразлично. Дешёвый хвост на ходовом
реагенте разбирают за МИНУТЫ. Пока ты не в игре, ты о нём не узнаешь
никак: аддоны в игре мертвы, когда игра закрыта. Здесь пуш из облака —
единственный способ вообще увидеть такую возможность.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import auction, blizzard, config, gameinfo, notify
from .analytics import auction as auction_analytics
from .analytics import events, report as build_report

STATE_FILE = config.DATA_DIR / "alert_state.json"

DEEP_BOTTOM_ENTER = 10.0  # входим в режим «глубокое дно»
DEEP_BOTTOM_EXIT = 15.0   # выходим (гистерезис, чтобы не мигать у порога)
EVENT_HORIZON_DAYS = 21   # за сколько дней предупреждать о событии

# Порог, начиная с которого находка достойна разбудить человека.
# В приложении порог ниже (500 з): там ты сам решил посмотреть, и
# показать мелочь не грех. Здесь мы ЛЕЗЕМ В КАРМАН с уведомлением, и
# цена ошибки другая — разбуженный ради двух тысяч золота человек
# отключит алерты совсем, и тогда пропустит настоящие.
ALERT_MIN_UPSIDE = 10_000.0

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


def _gold(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def _bargain_message(item: dict) -> str:
    """Сообщение про один слив. Все оговорки внутри — решать по нему."""
    lines = [
        f"💰 <b>{item['name']}</b>",
        "",
        f"Цена сейчас <b>{item['floor_gold']} з</b>, вернётся к {item['market_gold']} з.",
        f"Вложить {_gold(item['deal_cost_gold'])} з "
        f"({_gold(item['deal_qty'])} шт) → вернуть ~{_gold(item['upside_gold'])} з "
        f"чистыми ({item['roi_pct']:.0f}%).",
    ]

    if item.get("hours_to_clear") is not None:
        hours = item["hours_to_clear"]
        srok = f"{hours:.0f} ч" if hours < 48 else f"{hours / 24:.1f} сут"
        lines.append(f"Разойдётся примерно за {srok}.")
    else:
        lines.append("Скорость продаж по нему ещё не измерена — срок неизвестен.")

    if not item.get("current_era", True):
        lines += [
            "",
            "⚠️ Товар не из текущего дополнения. Большой разрыв у такого обычно "
            "оттого, что рынок никто не смотрит, — и продать бывает некому.",
        ]
    return "\n".join(lines)


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


def _worth_waking(item: dict) -> bool:
    """Стоит ли эта находка того, чтобы лезть к человеку в телефон.

    ПОРОГ ЗДЕСЬ СТРОЖЕ, ЧЕМ В ПРИЛОЖЕНИИ, и намеренно. В окне человек сам
    решил посмотреть, и находку с оговоркой показать не грех — он видит
    все числа разом и выбирает. Уведомление же приходит без спроса и
    требует действия: сходить в игру, потратить время и золото.

    Первый же сухой прогон показал, зачем это нужно. Наверх вылезли
    «Льняная ткань» с наваром 467 тысяч на 558 тысячах штук и «Огнецвет»
    на 528% — старьё с неизмеренной скоростью продаж. Арифметика верная,
    а совет вредный: столько льняной ткани не выкупит никто, и деньги
    застрянут навсегда.

    Правило: разбудить можно, если известно, что товар РАСХОДИТСЯ, — либо
    это подтверждено измерением, либо это товар текущего дополнения, где
    спрос заведомо есть. Старьё без измеренной скорости молчит до тех пор,
    пока скорость не появится.
    """
    if item.get("hours_to_clear") is not None:
        return True  # скорость измерена, а стоячие уже отсеяны в bargains
    return bool(item.get("current_era"))


def _auction_messages(prior: dict) -> tuple[list[str], list[int]]:
    """Сливы, которых не было в прошлый раз. Возвращает (сообщения, id находок).

    ДЕДУП ПО СОСТАВУ НАБОРА, а не по времени. Слив живёт часами, и алерт
    раз в час превратился бы в десять одинаковых сообщений про один и тот
    же лот. Поэтому шлём только те находки, которых в прошлый раз НЕ БЫЛО;
    когда находка уходит из набора, она забывается и в следующий раз
    сработает снова.
    """
    try:
        snapshot = auction.fetch(config.PRIMARY_REGION, blizzard.get_access_token())
        summary = auction_analytics.summary(
            config.PRIMARY_REGION, live=snapshot.quotes
        )
    except Exception as error:  # noqa: BLE001 — аукцион не должен ронять алерты жетона
        print(f"[KMARKET] Аукцион недоступен, пропускаю: {error}")
        return [], list(prior.get("auction_seen", []))

    worthy = [
        item
        for item in summary.get("bargains", [])
        if item["upside_gold"] >= ALERT_MIN_UPSIDE and _worth_waking(item)
    ]
    seen = set(prior.get("auction_seen", []))
    fresh = [item for item in worthy if item["item_id"] not in seen]

    messages = [_bargain_message(item) for item in fresh[:3]]
    if len(fresh) > 3:
        messages.append(
            f"…и ещё {len(fresh) - 3} находок помельче — смотри в приложении."
        )
    return messages, [item["item_id"] for item in worthy]


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

    # Сливы на товарном аукционе — отдельный, самый скоропортящийся повод.
    auction_texts, seen = _auction_messages(prior)
    messages.extend(auction_texts)
    state["auction_seen"] = seen

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
