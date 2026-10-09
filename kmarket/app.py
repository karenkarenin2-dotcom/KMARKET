"""KMARKET — точка входа приложения.

Окно на WebView2 через pywebview, как в KTRANS. Здесь только мост:
разбор вызовов из интерфейса и отправка событий обратно. Вся математика —
в kmarket.analytics.

ЧТО ПОКАЗЫВАЕТ ОКНО (упрощение 2026-10-09). Жетон и товары, которые
Карен выбрал сам. Слева список, справа один и тот же разбор для любого из
них: вердикт, график, уровни цены, недельный ритм, прошлый цикл. Товар
добавляется поиском по названию, и выбор уходит в облако — сборщик
начинает его писать.

ПОЧЕМУ НЕ САЙТ. Раньше дашборд был FastAPI + браузер. Это тянуло за собой
uvicorn, занятый порт, вкладку среди прочих вкладок и второй способ
запуска — то есть лишнюю точку отказа ради ничего.

ПОЧЕМУ ВСЁ ТЯЖЁЛОЕ — В ПОТОКЕ. Отчёт по жетону — это бэктест на пяти
годах истории, снимок аукциона — 3 МБ и пара секунд разбора. В
обработчике js_api это намертво вешает интерфейс. Поэтому bootstrap
отдаёт скелет мгновенно, а данные догоняют событиями.

ГРАБЛЯ ПРО evaluate_js. Строки обязательно через json.dumps — кириллица
и кавычки иначе рвут вызов.
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

import webview

from . import __version__, auction, blizzard, config, items, live, storage, tracked
from .analytics import frame
from .analytics.report import chart_data, invalidate, item_report, report, series_chart

UI_DIR = Path(__file__).resolve().parent / "ui"

WINDOW: webview.Window | None = None

# Сколько держать снимок аукциона в памяти. Blizzard обновляет его раз в
# час, так что чаще чем раз в четверть часа спрашивать бессмысленно, а
# весит он 3 МБ и разбирается пару секунд.
SNAPSHOT_TTL_SECONDS = 15 * 60

# Сколько результатов поиска показывать. Больше двух десятков строк —
# это уже не выбор, а прокрутка.
SEARCH_LIMIT = 20


def _emit(event: dict) -> None:
    if WINDOW is None:
        return
    try:
        payload = json.dumps(event, ensure_ascii=False)
        WINDOW.evaluate_js(f"window.kmarket.emit({payload})")
    except Exception:
        pass  # окно уже закрыто — событие никому не нужно


def _meta(item_id: int) -> dict:
    entry = items.get(item_id) or {}
    return {
        "key": str(item_id),
        "id": item_id,
        "name": items.name_of(item_id),
        "icon": entry.get("icon") or "",
        "quality": entry.get("quality") or "",
        "subclass": entry.get("subclass") or "",
    }


class Api:
    """Всё, что интерфейс может вызвать: pywebview.api.<метод>()."""

    def __init__(self) -> None:
        self._maximized = False
        self._snapshot: auction.Snapshot | None = None
        self._snapshot_at = 0.0
        self._history: dict[int, list[tuple]] = {}
        self._lock = threading.Lock()

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
    def assets(self) -> list[dict]:
        """Что показывать в левой колонке: жетон всегда первым, затем товары."""
        token = {"key": "token", "name": "Жетон WoW", "icon": "", "quality": "", "subclass": ""}
        return [token, *(_meta(item_id) for item_id in tracked.ids())]

    def bootstrap(self) -> dict:
        """Мгновенный скелет. Тяжёлое считается потом, в потоке."""
        return {
            "version": __version__,
            "regions": list(config.REGIONS),
            "primary": config.PRIMARY_REGION,
            "timezones": {"local": config.LOCAL_TZ, "server": config.SERVER_TZ},
            "assets": self.assets(),
        }

    def start_load(self, region: str) -> bool:
        """Пересчитать всё в фоне. Результат придёт событиями."""
        threading.Thread(target=self._load, args=(region, False), daemon=True).start()
        return True

    def refresh(self, region: str) -> bool:
        """Кнопка «обновить»: жетон и заново снятый снимок аукциона."""
        threading.Thread(target=self._load, args=(region, True), daemon=True).start()
        return True

    def start_token(self, region: str) -> bool:
        """Только жетон — при переключении EU/US товары пересчитывать незачем."""
        threading.Thread(target=self._load_token, args=(region,), daemon=True).start()
        return True

    def _load(self, region: str, refresh: bool) -> None:
        self._load_token(region)
        self._load_items(refresh)

    def _load_token(self, region: str) -> None:
        # Живая цена — первым делом: история на диске бывает на часы позади.
        try:
            if live.current_price(region) is not None:
                invalidate(region)
        except Exception as error:  # noqa: BLE001
            _emit({"type": "note", "text": f"Живая цена жетона недоступна: {error}"})
        try:
            _emit({"type": "report", "key": "token", "data": report(region)})
        except Exception as error:  # noqa: BLE001
            _emit({"type": "error", "text": f"Не удалось собрать отчёт по жетону: {error}"})

    # -- товары -------------------------------------------------------------
    def _fresh_snapshot(self, refresh: bool) -> auction.Snapshot | None:
        """Свежий снимок аукциона прямо у Blizzard, с кэшем на четверть часа.

        ПОЧЕМУ ЖИВОЙ ЗАПРОС, А НЕ ТОЛЬКО ИСТОРИЯ С ДИСКА. История попадает
        на диск через облако и git pull, и бывает на часы позади. Снимок у
        Blizzard тот же, что пишет облако, только без этой задержки.
        """
        with self._lock:
            young = time.monotonic() - self._snapshot_at < SNAPSHOT_TTL_SECONDS
            if self._snapshot is not None and young and not refresh:
                return self._snapshot
            try:
                self._snapshot = auction.fetch(
                    config.PRIMARY_REGION, blizzard.get_access_token()
                )
                self._snapshot_at = time.monotonic()
            except Exception as error:  # noqa: BLE001
                _emit({"type": "note", "text": f"Аукцион сейчас недоступен: {error}"})
            return self._snapshot

    def _item_rows(self, item_id: int) -> list[tuple]:
        if item_id not in self._history:
            self._history.update(storage.load_auction(config.PRIMARY_REGION, {item_id}))
        return self._history.get(item_id, [])

    def _live(self, item_id: int):
        snap = self._snapshot
        if snap is None or item_id not in snap.quotes:
            return None
        return (snap.updated, snap.quotes[item_id])

    def _item_report(self, item_id: int) -> dict:
        data = item_report(self._item_rows(item_id), live=self._live(item_id))
        return data | {"meta": _meta(item_id)}

    def _load_items(self, refresh: bool) -> None:
        ids = tracked.ids()
        if not ids:
            return
        self._history = storage.load_auction(config.PRIMARY_REGION, set(ids))
        self._fresh_snapshot(refresh)
        for item_id in ids:
            try:
                _emit({"type": "report", "key": str(item_id), "data": self._item_report(item_id)})
            except Exception as error:  # noqa: BLE001
                _emit({"type": "error", "text": f"{items.name_of(item_id)}: {error}"})

    # -- поиск и список слежки ----------------------------------------------
    def search(self, query: str) -> bool:
        """Найти товар. Результат придёт событием `search`."""
        threading.Thread(target=self._search, args=(query,), daemon=True).start()
        return True

    def _search(self, query: str) -> None:
        try:
            token = blizzard.get_access_token()
            found = items.search(query, token)
            snap = self._fresh_snapshot(refresh=False)
            if snap is None:
                _emit({"type": "search", "query": query, "error": "аукцион недоступен"})
                return
            # Оставляем только то, что торгуется на товарном аукционе ПРЯМО
            # СЕЙЧАС: остальное (шмот, петы) живёт на реалмовых аукционах,
            # которые мы не собираем, и следить за ним было бы нечем.
            text = query.strip().lower()
            hits = [f for f in found if f["id"] in snap.quotes]
            hits.sort(key=lambda f: (f["name"].lower() != text, f["name"]))
            hits = hits[:SEARCH_LIMIT]
            if hits:
                items.resolve([h["id"] for h in hits], token)
            history = storage.load_auction(config.PRIMARY_REGION, {h["id"] for h in hits})
            have = set(tracked.ids())
            results = []
            for hit in hits:
                quote = snap.quotes[hit["id"]]
                rows = history.get(hit["id"]) or []
                results.append(
                    _meta(hit["id"])
                    | {
                        "price": round(quote.market / blizzard.COPPER_PER_GOLD, 2),
                        "floor": round(quote.floor / blizzard.COPPER_PER_GOLD, 2),
                        "since": rows[0][0].isoformat() if rows else None,
                        "tracked": hit["id"] in have,
                    }
                )
            _emit({"type": "search", "query": query, "results": results, "total": len(found)})
        except Exception as error:  # noqa: BLE001
            _emit({"type": "search", "query": query, "error": str(error)})

    def track(self, item_id: int) -> bool:
        threading.Thread(target=self._change, args=(int(item_id), True), daemon=True).start()
        return True

    def untrack(self, item_id: int) -> bool:
        threading.Thread(target=self._change, args=(int(item_id), False), daemon=True).start()
        return True

    def _change(self, item_id: int, add: bool) -> None:
        """Изменить список слежки и отправить его в облако.

        Облако — единственное место, где идёт сбор, поэтому список без
        отправки не значит ничего. Не вышло отправить — говорим прямо и
        ничего не меняем, чтобы окно не обещало слежку, которой нет.
        """
        try:
            entries = tracked.load()
            if add:
                if item_id in {e["id"] for e in entries}:
                    return
                items.resolve([item_id], blizzard.get_access_token())
                entries.append({"id": item_id, "name": items.name_of(item_id)})
                message = f"track: {items.name_of(item_id)}"
            else:
                entries = [e for e in entries if e["id"] != item_id]
                message = f"untrack: {items.name_of(item_id)}"
            files = {"data/tracked.json": tracked.render(entries)}
            # Справочник едет тем же коммитом: в нём имя и иконка нового
            # товара. Иначе он остался бы вечно «изменённым» локально.
            if items.CACHE_PATH.exists():
                files["data/items.json"] = items.CACHE_PATH.read_text(encoding="utf-8")
            outcome = tracked.publish(files, message)
        except Exception as error:  # noqa: BLE001
            _emit({"type": "tracked", "ok": False, "text": f"Не получилось: {error}"})
            return

        _emit(
            {
                "type": "tracked",
                "ok": True,
                "key": str(item_id),
                "added": add,
                "assets": self.assets(),
                "text": f"{items.name_of(item_id)}: {outcome}.",
            }
        )
        if add:
            try:
                _emit({"type": "report", "key": str(item_id), "data": self._item_report(item_id)})
            except Exception as error:  # noqa: BLE001
                _emit({"type": "error", "text": str(error)})

    # -- данные по запросу --------------------------------------------------
    def get_chart(self, key: str, region: str, days: int) -> dict:
        try:
            if key == "token":
                return chart_data(region, int(days))
            item_id = int(key)
            rows = list(self._item_rows(item_id))
            fresh = self._live(item_id)
            if fresh and (not rows or fresh[0] > rows[-1][0]):
                rows.append((fresh[0], fresh[1].floor, fresh[1].market, fresh[1].quantity))
            return series_chart(frame.item_series(rows), int(days))
        except Exception as error:  # noqa: BLE001
            return {"error": str(error)}


# Потолок кэша WebView2. Один сеанс набирает 30–70 МБ (замерено), и без
# подрезки папка растёт от запуска к запуску без всякого предела.
CACHE_LIMIT_MB = 120


def _cache_size_mb(path: Path) -> float:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue  # файл держит вебвью — не наше дело
    return total / 1024 / 1024


def _trim_cache() -> None:
    """Снести кэш целиком, если он перерос потолок.

    Именно целиком, а не выборочно: WebView2 сам разберётся, что ему
    нужно, и заново скачает недостающее. Выборочная чистка внутри чужого
    кэша — это способ получить непредсказуемо сломанный движок.

    Мягко: занятый файл не повод ронять запуск, останется до следующего
    раза (тот же приём, что в KFRAME/tidy.rs).
    """
    try:
        if _cache_size_mb(config.CACHE_DIR) < CACHE_LIMIT_MB:
            return
        shutil.rmtree(config.CACHE_DIR, ignore_errors=True)
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[KMARKET] Кэш WebView2 перерос {CACHE_LIMIT_MB} МБ — очищен.")
    except OSError:
        pass


def _drop_stale_cache() -> None:
    """Снести кэш WebView2, если вёрстка изменилась с прошлого запуска.

    ПОЧЕМУ ЭТО ОБЯЗАТЕЛЬНО (поймано 2026-08-09). Карен обновил приложение
    и увидел ПОЛОВИНУ правки: заголовки секций отзывались на клик (новый
    app.js), а сворачиваться отказывались (старый style.css из кэша).
    Выглядит это как поломка логики, и искать её человек идёт не туда —
    в саму логику.

    Считаем отпечаток по времени изменения файлов ui/. Совпал — кэш
    оставляем (он экономит запуск), разошёлся — сносим целиком: пусть
    WebView2 перечитает всё. Выборочная чистка чужого кэша — способ
    получить непредсказуемо сломанный движок.
    """
    try:
        stamp = "|".join(
            f"{path.name}:{path.stat().st_mtime_ns}"
            for path in sorted(UI_DIR.glob("*"))
            if path.is_file()
        )
        marker = config.CACHE_DIR / "ui.stamp"
        if marker.exists() and marker.read_text(encoding="utf-8") == stamp:
            return
        if config.CACHE_DIR.exists():
            shutil.rmtree(config.CACHE_DIR, ignore_errors=True)
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text(stamp, encoding="utf-8")
        print("[KMARKET] Вёрстка изменилась — кэш WebView2 сброшен.")
    except OSError:
        pass  # не смогли — значит покажем как есть, ронять запуск незачем


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

    # ПОЧЕМУ private_mode=False И СВОЙ storage_path (замерено 2026-08-07).
    # По умолчанию pywebview включает приватный режим и на КАЖДЫЙ запуск
    # заводит новую папку кэша WebView2 в %TEMP%\tmp*, а при выходе её не
    # удаляет. На машине набралось девять брошенных папок на 106 МБ, из
    # них два сеанса KMARKET — 30 и 69 МБ. Со своей постоянной папкой
    # кэш один на все запуски, и мы можем сами его подрезать.
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _trim_cache()
    # После подрезки по размеру — проверка на свежесть вёрстки. Порядок
    # важен: _trim_cache может снести папку целиком, и отпечаток тогда
    # запишется в чистую.
    _drop_stale_cache()

    try:
        webview.start(
            gui="edgechromium",
            debug="--debug" in sys.argv,
            private_mode=False,
            storage_path=str(config.CACHE_DIR),
        )
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
