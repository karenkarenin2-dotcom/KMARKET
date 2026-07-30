"""Запуск дашборда: python -m kmarket.web

Поднимает сервер на localhost и открывает браузер. `KMARKET.bat` зовёт
ровно это.

ВЕСЬ ТЕКСТ ДЛЯ ЧЕЛОВЕКА ЖИВЁТ ЗДЕСЬ, А НЕ В .BAT. Батник cmd.exe читает
в кодировке консоли (на русской Windows это cp866), поэтому кириллица в
нём превращается в мусор и ломает разбор файла. Python пишет в консоль
через Unicode-API и от кодовой страницы не зависит.
"""

from __future__ import annotations

import socket
import subprocess
import threading
import time
import webbrowser

from .. import config

HOST = "127.0.0.1"  # только локально: наружу дашборд не смотрит
PORT = 8765  # не 8000 — тот порт занят чем угодно на машине разработчика


def _sync_history() -> None:
    """Подтянуть свежую историю из облака перед стартом.

    Сборщик пишет данные в GitHub, дашборд читает их с диска — без этого
    подтягивания дашборд показывал бы историю на момент последнего pull.

    ТОЛЬКО --ff-only, НИКОГДА --rebase --autostash (урок 2026-07-26).
    Прежний вариант с автозаначкой при неудачном возврате вписывал маркеры
    конфликта ПРЯМО В ФАЙЛЫ ДАННЫХ, ломал историю и оставлял git в
    состоянии `UU`. Запуск дашборда обязан быть безопасной операцией:
    fast-forward либо ничего не делает, либо просто доматывает коммиты —
    он физически не может тронуть рабочие файлы и что-то испортить.

    Не удалось — не беда: работаем на локальной копии, а живой запрос цены
    (kmarket.live) всё равно освежит заголовок.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(config.ROOT), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("[KMARKET] История не обновлена (git недоступен) — работаю на локальной копии.")
        return
    if result.returncode == 0:
        tail = (result.stdout or "").strip().splitlines()
        print(f"[KMARKET] История актуальна: {tail[-1] if tail else 'обновлений нет'}")
    else:
        # Причину печатаем: молчаливое «не обновилось» уже однажды скрыло
        # застрявший конфликт, и дашборд неделю показывал старые данные.
        reason = (result.stderr or result.stdout or "").strip().splitlines()
        print("[KMARKET] История не обновлена — работаю на локальной копии.")
        if reason:
            print(f"[KMARKET]   причина: {reason[0]}")


def _port_is_busy(host: str, port: int) -> bool:
    """Кто-то уже слушает порт? Иначе uvicorn упадёт с невнятной ошибкой."""
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def _open_when_ready(url: str, timeout: float = 30.0) -> None:
    """Открыть браузер, как только сервер начал принимать соединения."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_is_busy(HOST, PORT):  # порт занят НАМИ = сервер слушает
            webbrowser.open(url)
            return
        time.sleep(0.25)
    print(f"[KMARKET] Сервер не поднялся за {timeout:.0f} с — открой вручную: {url}")


def main() -> int:
    url = f"http://{HOST}:{PORT}/"

    if _port_is_busy(HOST, PORT):
        print(f"[KMARKET] Порт {PORT} уже занят — похоже, дашборд запущен в другом окне.")
        print(f"[KMARKET] Открываю {url} в браузере; второй сервер не нужен.")
        webbrowser.open(url)
        return 0

    _sync_history()

    import uvicorn  # импорт здесь, чтобы проверка порта прошла до тяжёлой загрузки

    print(f"[KMARKET] Дашборд: {url}")
    print("[KMARKET] Первый расчёт занимает несколько секунд — считаются события и бэктест.")
    print("[KMARKET] Остановить: Ctrl+C или просто закрой это окно.")

    # Браузер открываем, ДОЖДАВШИСЬ готовности порта, а не по таймеру.
    # Прежние 1.5 секунды были угадыванием: на холодном старте uvicorn
    # иногда ещё не слушал порт, браузер получал «не удалось подключиться»,
    # и выглядело это как «скрипт не всегда открывает сайт».
    opener = threading.Thread(target=_open_when_ready, args=(url,), daemon=True)
    opener.start()

    try:
        uvicorn.run("kmarket.web.app:app", host=HOST, port=PORT, log_level="warning")
    except KeyboardInterrupt:  # Ctrl+C — это штатный выход, а не авария
        pass

    print("[KMARKET] Сервер остановлен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
