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
import webbrowser

from .. import config

HOST = "127.0.0.1"  # только локально: наружу дашборд не смотрит
PORT = 8765  # не 8000 — тот порт занят чем угодно на машине разработчика


def _sync_history() -> None:
    """Подтянуть свежую историю из облака перед стартом.

    Сборщик пишет данные в GitHub, дашборд читает их с диска — без этого
    подтягивания дашборд показывал бы историю на момент последнего pull.
    Тихо и без фатальных последствий: нет git, нет сети, есть локальные
    правки — просто работаем на том, что уже есть на диске. Живой запрос
    цены (kmarket.live) всё равно освежит заголовок.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(config.ROOT), "pull", "--rebase", "--autostash"],
            capture_output=True,
            text=True,
            timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("[KMARKET] История не обновлена (git недоступен) — работаю на локальной копии.")
        return
    if result.returncode == 0:
        line = (result.stdout or "").strip().splitlines()[-1:] or ["обновлено"]
        print(f"[KMARKET] История актуальна: {line[0]}")
    else:
        print("[KMARKET] История не обновлена — работаю на локальной копии.")


def _port_is_busy(host: str, port: int) -> bool:
    """Кто-то уже слушает порт? Иначе uvicorn упадёт с невнятной ошибкой."""
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


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

    # Браузер открываем с задержкой, чтобы сервер успел подняться.
    # daemon=True — иначе таймер держал бы процесс живым после остановки.
    opener = threading.Timer(1.5, lambda: webbrowser.open(url))
    opener.daemon = True
    opener.start()

    try:
        uvicorn.run("kmarket.web.app:app", host=HOST, port=PORT, log_level="warning")
    except KeyboardInterrupt:  # Ctrl+C — это штатный выход, а не авария
        pass

    print("[KMARKET] Сервер остановлен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
