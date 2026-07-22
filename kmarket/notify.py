"""Отправка алертов в Telegram (бот @kmarketwowbot).

Ноль зависимостей — шлётся и локально, и прямо из GitHub Actions, поэтому
пуш приходит, даже когда компьютер выключен.

ПРИНЦИП: алерты необязательны и НИКОГДА не роняют сбор. Нет токена, нет
интернета, Telegram лежит — функция вернёт False и промолчит. Потерянный
алерт восстановим (цена никуда не денется), потерянная точка истории — нет.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import config

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 15


def is_configured() -> bool:
    return bool(config.optional("TELEGRAM_BOT_TOKEN") and config.optional("TELEGRAM_CHAT_ID"))


def send(text: str, *, silent: bool = False) -> bool:
    """Отправить сообщение. True — ушло, False — не настроено или не дошло.

    `silent=True` — уведомление без звука (для рутины вроде отчёта о сборе).
    """
    token = config.optional("TELEGRAM_BOT_TOKEN")
    chat_id = config.optional("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": "true" if silent else "false",
            "link_preview_options": json.dumps({"is_disabled": True}),
        }
    ).encode("utf-8")

    try:
        request = urllib.request.Request(
            API.format(token=token, method="sendMessage"), data=payload
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return bool(json.loads(response.read().decode("utf-8")).get("ok"))
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        print(f"[KMARKET] Алерт не ушёл: {error}", file=sys.stderr)
        return False
