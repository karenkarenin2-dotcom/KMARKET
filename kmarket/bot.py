"""Интерактивный Telegram-бот KMARKET (@kmarketwowbot).

Спросить цену с телефона, не поднимая дашборд. Команды:

    /price    текущая цена EU и US, живьём у Blizzard
    /verdict  полный вердикт с объяснением
    /season   когда на неделе дешевле всего
    /events   ближайшие патчи и их влияние на цену
    /help     список команд

ДВА РЕЖИМА ЗАПУСКА:

    python -m kmarket.bot          длинный опрос, отвечает мгновенно
    python -m kmarket.bot --once   разобрать накопившееся и выйти (для крона)

Первый режим — когда компьютер включён. Второй нужен, чтобы бот отвечал и
при выключенном ПК: его можно вызывать из GitHub Actions по расписанию,
ценой задержки в размер интервала крона.

ТРАНСПОРТ БЕЗ ЗАВИСИМОСТЕЙ (urllib), а тяжёлая аналитика импортируется
ЛЕНИВО: /price обязана работать даже там, где pandas не стоит.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import config, live

API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 30  # длинный опрос: соединение висит, пока нет событий

# Порог «слишком старой» команды при ПЕРВОМ проходе.
#
# ЭТО БЫЛА ГЛАВНАЯ ПРИЧИНА «бот не работает» (2026-07-26). Порог стоял 5
# минут — и получалось так: Карен жал команды, пока бот был выключен,
# потом запускал бота, тот забирал сообщения из очереди, считал их
# устаревшими и МОЛЧА выбрасывал. Со стороны — «нажимаю, ничего не
# происходит», причём очередь опустошалась, так что и следов не оставалось.
#
# Теперь сутки: команда, отправленная пока бот спал, будет отвечена при
# запуске — это ровно то, чего человек ждёт. И самое важное: пропуск
# больше НЕ МОЛЧАЛИВЫЙ, в консоль печатается сколько и почему пропущено.
STALE_SECONDS = 86_400  # сутки

HELP = (
    "<b>KMARKET</b> — аналитика жетона WoW (EU)\n\n"
    "/price — текущая цена, живьём у Blizzard\n"
    "/verdict — брать сейчас или ждать, с объяснением\n"
    "/season — когда на неделе дешевле всего\n"
    "/events — ближайшие патчи и их влияние\n"
    "/help — эта справка"
)


def _gold(value: float) -> str:
    return f"{round(value):,}".replace(",", " ")


def _api(method: str, **params) -> dict:
    token = config.require("TELEGRAM_BOT_TOKEN")
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(request, timeout=POLL_TIMEOUT + 15) as response:
        return json.loads(response.read().decode("utf-8"))


def send(chat_id: int | str, text: str) -> bool:
    try:
        result = _api("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")
        return bool(result.get("ok"))
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        print(f"[KMARKET] Ответ не ушёл: {error}", file=sys.stderr)
        return False


# --------------------------------------------------------------------- команды


def cmd_price() -> str:
    lines = ["<b>Цена жетона сейчас</b>", ""]
    for region in config.REGIONS:
        price = live.current_price(region)
        if price is None:
            lines.append(f"{region.upper()}: не отвечает Blizzard")
            continue
        age = round((datetime.now(timezone.utc) - price.updated).total_seconds() / 60)
        lines.append(f"<b>{region.upper()}</b>  {_gold(price.gold)} g   <i>{age} мин назад</i>")

    percentile = _percentile_line()
    if percentile:
        lines += ["", percentile]
    return "\n".join(lines)


def _percentile_line() -> str | None:
    """Где цена относительно истории. Требует pandas — потому и отдельно."""
    try:
        from .analytics import report as build_report
    except ImportError:
        return None
    try:
        report = build_report(config.PRIMARY_REGION)
        window = next(w for w in report["windows"] if w["days"] == 90)
    except Exception:  # noqa: BLE001 — цена важнее аналитики, без неё не падаем
        return None
    return (
        f"EU на <b>{window['percentile']:.0f}-м перцентиле</b> за 90 дней "
        f"(от {_gold(window['low'])} до {_gold(window['high'])} g)"
    )


def cmd_verdict() -> str:
    from .analytics import report as build_report

    report = build_report(config.PRIMARY_REGION, fresh=True)
    if report.get("empty"):
        return "Истории пока нет — вердикт считать не на чем."
    verdict = report["verdict"]
    emoji = {"buy": "🟢", "wait": "🟡", "avoid": "🔴"}.get(verdict["state"], "")
    lines = [
        f"{emoji} <b>{verdict['title']}</b> — {verdict['summary']}",
        f"{_gold(report['current']['price'])} g",
        "",
    ]
    lines += [f"• {reason}" for reason in verdict["reasons"]]
    lines += ["", f"<i>Уверенность: {verdict['confidence']}</i>"]
    return "\n".join(lines)


def cmd_season() -> str:
    from .analytics import report as build_report

    report = build_report(config.PRIMARY_REGION)
    season = report.get("seasonality")
    if not season:
        return "Данных для сезонности пока мало."
    best = "\n".join(
        f"• {c['weekday']} {c['hour']:02d}:00 — {c['deviation']:+.2f}%" for c in season["best"][:3]
    )
    worst = "\n".join(
        f"• {c['weekday']} {c['hour']:02d}:00 — {c['deviation']:+.2f}%" for c in season["worst"][:3]
    )
    return (
        f"<b>Когда дешевле</b> (время серверов EU)\n\n"
        f"Дешевле всего:\n{best}\n\n"
        f"Дороже всего:\n{worst}\n\n"
        f"<i>Разброс внутри недели — около 8%. Это эффект ресета: "
        f"перед ним жетоны скупают за золото.</i>"
    )


def cmd_events() -> str:
    from .analytics import events

    ahead = events.upcoming(3)
    if not ahead:
        head = "Ближайших анонсированных событий нет.\n"
    else:
        head = "<b>Впереди:</b>\n" + "\n".join(
            f"• через {e['in_days']} дн — {e['label']}" for e in ahead
        ) + "\n"
    return (
        f"{head}\n"
        "<b>Как события двигают цену</b> (по 18 событиям):\n"
        "• Запуск дополнения: +12% за месяц ДО, −12% ПОСЛЕ\n"
        "• Старт сезона: +7% ДО, −5% ПОСЛЕ\n\n"
        "<i>Перед стартом контента жетоны массово скупают за золото — "
        "цена задрана. После запуска оседает.</i>"
    )


COMMANDS = {
    "/start": lambda: HELP,
    "/help": lambda: HELP,
    "/price": cmd_price,
    "/verdict": cmd_verdict,
    "/season": cmd_season,
    "/events": cmd_events,
}


def reply_for(text: str) -> str:
    command = text.strip().split()[0].split("@")[0].lower() if text.strip() else ""
    handler = COMMANDS.get(command)
    if handler is None:
        return f"Не знаю такой команды.\n\n{HELP}"
    try:
        return handler()
    except Exception as error:  # noqa: BLE001 — бот не должен умирать от одной команды
        print(f"[KMARKET] Ошибка в {command}: {error}", file=sys.stderr)
        return "Не смог посчитать — что-то пошло не так. Попробуй ещё раз."


# ------------------------------------------------------------------ цикл опроса


def _handle(update: dict) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = message.get("text")
    if not text or "id" not in chat:
        return
    print(f"[KMARKET] {chat['id']}: {text}")
    send(chat["id"], reply_for(text))


def _announce() -> bool:
    """Назвать себя в консоли и отметиться в чате. False — если бот не готов.

    Раньше запуск был безмолвным, и отличить «бот работает, но не отвечает»
    от «бот вообще не поднялся» было нельзя. Теперь видно И кто именно
    запустился (ник важен: у Карена есть второй бот с похожим именем), И
    что доставка в чат работает.
    """
    try:
        me = _api("getMe").get("result", {})
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        print(f"[KMARKET] Telegram не отвечает: {error}", file=sys.stderr)
        return False
    username = me.get("username", "?")
    print(f"[KMARKET] Бот @{username} (id {me.get('id')}) на связи.")

    chat_id = config.optional("TELEGRAM_CHAT_ID")
    if chat_id:
        ok = send(chat_id, "🟢 <b>Бот KMARKET запущен</b>\nЖду команды: /price /verdict /season /events")
        print(f"[KMARKET] Проверка чата: {'сообщение доставлено' if ok else 'НЕ ДОСТАВЛЕНО'}")
    return True


def run(once: bool = False) -> int:
    if not config.optional("TELEGRAM_BOT_TOKEN"):
        print("[KMARKET] Не задан TELEGRAM_BOT_TOKEN — боту нечем работать.", file=sys.stderr)
        return 1
    if not _announce():
        return 1

    offset = None
    # На первом проходе выбрасываем совсем древние команды (см. STALE_SECONDS).
    fresh_only = True

    print("[KMARKET] Слушаю команды. Остановить — Ctrl+C или закрыть окно.")
    while True:
        try:
            params = {"timeout": 0 if once else POLL_TIMEOUT}
            if offset is not None:
                params["offset"] = offset
            result = _api("getUpdates", **params)
        except urllib.error.HTTPError as error:
            # 409 = того же бота уже опрашивает другой процесс. Частый случай:
            # KMARKET_BOT.bat запущен дважды. Без явного сообщения человек
            # видел бы бесконечный поток непонятных ошибок.
            if error.code == 409:
                print(
                    "[KMARKET] Этого бота уже слушает другая копия.\n"
                    "[KMARKET] Закрой лишнее окно KMARKET_BOT — двум сразу Telegram работать не даёт.",
                    file=sys.stderr,
                )
                return 1
            print(f"[KMARKET] Опрос не удался: HTTP {error.code}", file=sys.stderr)
            if once:
                return 1
            time.sleep(5)
            continue
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            print(f"[KMARKET] Опрос не удался: {error}", file=sys.stderr)
            if once:
                return 1
            time.sleep(5)
            continue

        updates = result.get("result", [])
        now = time.time()
        skipped = 0
        for update in updates:
            offset = update["update_id"] + 1
            sent_at = (update.get("message") or {}).get("date") or now
            if fresh_only and now - sent_at > STALE_SECONDS:
                skipped += 1  # подтверждаем и забываем, но НЕ молча (см. ниже)
                continue
            _handle(update)
        if skipped:
            print(f"[KMARKET] Пропущено старых команд (больше суток): {skipped}")
        fresh_only = False

        if once:
            print(f"[KMARKET] Разобрано обновлений: {len(updates)}")
            return 0


if __name__ == "__main__":
    raise SystemExit(run(once="--once" in sys.argv))
