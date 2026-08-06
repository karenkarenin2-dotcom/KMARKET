"""KMARKET — точка входа приложения.

Окно на WebView2 через pywebview, как в KTRANS. Здесь только мост:
разбор вызовов из интерфейса и отправка событий обратно. Вся математика —
в kmarket.analytics.

ПОЧЕМУ НЕ САЙТ. Раньше дашборд был FastAPI + браузер. Это тянуло за собой
uvicorn, занятый порт, вкладку среди прочих вкладок и второй способ
запуска — то есть лишнюю точку отказа ради ничего. Окну на WebView2 не
нужен ни порт, ни сервер, а вёрстка остаётся той же.

ПОЧЕМУ ВСЁ ТЯЖЁЛОЕ — В ПОТОКЕ. Сборка отчёта это перебор десятков тысяч
срезов истории (бэктест), несколько секунд работы. В обработчике js_api
это намертво вешает интерфейс: окно белеет и не отвечает. Поэтому
bootstrap отдаёт скелет мгновенно, а данные догоняют событиями.

ГРАБЛЯ ПРО evaluate_js. Строки обязательно через json.dumps — кириллица
и кавычки иначе рвут вызов. Крупные данные (список аукциона на 80
предметов) событиями не гоняем: интерфейс забирает их вызовом
api.get_auction(), возвращаемое значение сериализуется нормальным путём.
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
from pathlib import Path

import webview

from . import __version__, config, live
from .analytics import auction as auction_analytics
from .analytics.report import chart_data, invalidate, report

UI_DIR = Path(__file__).resolve().parent / "ui"

WINDOW: webview.Window | None = None


def _emit(event: dict) -> None:
    if WINDOW is None:
        return
    try:
        payload = json.dumps(event, ensure_ascii=False)
        WINDOW.evaluate_js(f"window.kmarket.emit({payload})")
    except Exception:
        pass  # окно уже закрыто — событие никому не нужно


class Api:
    """Всё, что интерфейс может вызвать: pywebview.api.<метод>()."""

    def __init__(self) -> None:
        self._maximized = False
        self._auction_cache: dict | None = None

    # -- окно ---------------------------------------------------------------
    def window_action(self, action: str) -> bool:
        """Возвращает состояние «развёрнуто» — по нему шапка меняет рисунок.

        Состояние держим сами: pywebview о нём не сообщает, а frameless-окно
        система разворачивать не умеет. Заодно frameless отключает
        изменение размера мышью, поэтому кнопка «развернуть» обязательна.
        """
        if WINDOW is None:
            return False
        if action == "minimize":
            WINDOW.minimize()
        elif action == "maximize":
            if self._maximized:
                WINDOW.restore()
            else:
                WINDOW.maximize()
            self._maximized = not self._maximized
        elif action == "close":
            WINDOW.destroy()
        return self._maximized

    # -- стартовые данные ---------------------------------------------------
    def bootstrap(self) -> dict:
        """Мгновенный скелет. Тяжёлое считается потом, в потоке."""
        return {
            "version": __version__,
            "regions": list(config.REGIONS),
            "primary": config.PRIMARY_REGION,
            "timezones": {"local": config.LOCAL_TZ, "server": config.SERVER_TZ},
        }

    def start_load(self, region: str) -> bool:
        """Запустить пересчёт в фоне. Результат придёт событиями."""
        threading.Thread(target=self._load, args=(region,), daemon=True).start()
        return True

    def _load(self, region: str) -> None:
        # Живая цена — быстро и первым делом: заголовок должен ожить сразу.
        try:
            price = live.current_price(region)
            if price is not None:
                invalidate(region)
                _emit(
                    {
                        "type": "live",
                        "region": region,
                        "price": price.gold,
                        "updated_utc": price.updated.isoformat(),
                    }
                )
        except Exception as error:  # noqa: BLE001
            _emit({"type": "note", "text": f"Живая цена недоступна: {error}"})

        try:
            _emit({"type": "report", "data": report(region)})
        except Exception as error:  # noqa: BLE001
            _emit({"type": "error", "text": f"Не удалось собрать отчёт: {error}"})
            return

        try:
            self._auction_cache = auction_analytics.summary(region)
            _emit({"type": "auction-ready", "tracked": self._auction_cache["tracked"]})
        except Exception as error:  # noqa: BLE001
            _emit({"type": "note", "text": f"Аукцион недоступен: {error}"})

    # -- данные по запросу --------------------------------------------------
    def get_chart(self, region: str, days: int) -> dict:
        try:
            return {"region": region, "days": days, **chart_data(region, int(days))}
        except Exception as error:  # noqa: BLE001
            return {"error": str(error)}

    def get_auction(self) -> dict:
        """Крупные данные — возвращаемым значением, не через evaluate_js."""
        return self._auction_cache or {"items": [], "bargains": [], "tracked": 0}


def main() -> int:
    global WINDOW

    api = Api()
    WINDOW = webview.create_window(
        "KMARKET",
        str(UI_DIR / "index.html"),
        js_api=api,
        width=1180,
        height=800,
        min_size=(940, 660),
        background_color="#20201F",
        frameless=True,
        easy_drag=False,  # тащим за заголовок, а не за любое пустое место
        text_select=True,
    )

    try:
        webview.start(gui="edgechromium", debug="--debug" in sys.argv)
    except Exception:
        traceback.print_exc()
        print(
            "\nНе удалось открыть окно. Скорее всего, нет WebView2 Runtime.\n"
            "В Windows 11 он предустановлен; если нет — поставьте командой:\n"
            "  winget install Microsoft.EdgeWebView2Runtime\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
