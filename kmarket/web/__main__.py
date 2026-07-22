"""Запуск дашборда: python -m kmarket.web

Поднимает сервер на localhost и открывает браузер. Ярлык на рабочем столе
зовёт ровно это.
"""

from __future__ import annotations

import threading
import webbrowser

import uvicorn

HOST = "127.0.0.1"
PORT = 8765  # не 8000: тот порт занят чем угодно на машине разработчика


def main() -> None:
    url = f"http://{HOST}:{PORT}/"
    print(f"[KMARKET] Дашборд: {url}")
    print("[KMARKET] Первый расчёт занимает несколько секунд — считается бэктест.")
    # Браузер открываем с задержкой, чтобы сервер успел подняться.
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("kmarket.web.app:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
