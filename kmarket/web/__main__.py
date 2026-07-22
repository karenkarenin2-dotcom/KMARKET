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
import threading
import webbrowser

HOST = "127.0.0.1"  # только локально: наружу дашборд не смотрит
PORT = 8765  # не 8000 — тот порт занят чем угодно на машине разработчика


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
