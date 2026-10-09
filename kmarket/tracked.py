"""Что отслеживаем на аукционе: выбор Карена плюс тихий фон реагентов.

Ноль зависимостей: `collect_ids()` зовёт облачный сборщик.

ДВА СПИСКА, ДВА ПИСАТЕЛЯ.

* `data/tracked.json` — товары, которые Карен выбрал сам. Их видно в окне.
  Пишет ТОЛЬКО приложение, кнопками «следить» / «не следить».
* `data/reagents.json` — реагенты рецептов Midnight. Облако пишет их молча,
  в окне их нет. Зачем: история по товару нужна ДО того, как он стал
  интересен. Реагент, добавленный в слежку после подорожания, бесполезен,
  а прошлое у Blizzard не отматывается.

КАК ВЫБОР ПОПАДАЕТ В ОБЛАКО. Сборщик живёт в GitHub Actions и читает
`tracked.json` из репозитория. Значит приложение обязано закоммитить и
отправить файл само. Делается это НЕ обычным `git commit`, а сборкой
коммита поверх `origin/main` на голых командах git (`publish`):

  * рабочее дерево и HEAD не трогаются, пока push не прошёл, — если сеть
    отвалилась на полпути, локально ничего не испорчено;
  * никакого rebase и merge с данными (правило 2026-08-07: сливать файлы
    данных git не умеет, и просить его об этом нельзя);
  * проиграл гонку облачному сборщику — просто собираем коммит заново
    поверх его свежего коммита. Наши файлы сборщик не трогает никогда,
    поэтому конфликтовать нечему.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone

from . import config

# Жетон в списке не живёт: он отслеживается всегда, и сборщик у него свой.
# Этот список — только товары аукциона, по id предмета.


def _read(path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load() -> list[dict]:
    """Выбор Карена: [{"id": 259085, "name": "..."}], в порядке добавления."""
    out = []
    for entry in _read(config.TRACKED_FILE).get("items") or []:
        try:
            out.append({"id": int(entry["id"]), "name": str(entry.get("name") or "")})
        except (KeyError, TypeError, ValueError):
            continue  # битая запись не повод терять весь список
    return out


def ids() -> list[int]:
    return [entry["id"] for entry in load()]


def reagent_ids() -> list[int]:
    out = []
    for key in (_read(config.REAGENTS_FILE).get("items") or {}):
        try:
            out.append(int(key))
        except ValueError:
            continue
    return out


def collect_ids() -> list[int]:
    """Всё, что пишет облако: выбранное впереди, фон реагентов следом."""
    return list(dict.fromkeys([*ids(), *reagent_ids()]))


def render(entries: list[dict]) -> str:
    """Содержимое tracked.json. Имя пишем рядом с id — файл читается глазами."""
    payload = {
        "note": "Товары, которые отслеживает KMARKET. Правится из приложения.",
        "items": [{"id": int(e["id"]), "name": e.get("name") or ""} for e in entries],
    }
    return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


# --------------------------------------------------------------------------
# Публикация в облако.
# --------------------------------------------------------------------------


def _git(*args: str, env: dict | None = None, data: bytes | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(config.ROOT), *args],
        input=data,
        capture_output=True,
        timeout=60,
        env=env,
    )
    if result.returncode != 0:
        reason = (result.stderr or result.stdout or b"").decode("utf-8", "replace")
        raise RuntimeError(reason.strip().splitlines()[-1] if reason.strip() else "git отказал")
    return result.stdout.decode("utf-8", "replace").strip()


def _commit_on_origin(files: dict[str, str], message: str) -> str:
    """Собрать коммит «origin/main + наши файлы», не трогая рабочее дерево."""
    _git("fetch", "-q", "origin", "main")
    base = _git("rev-parse", "origin/main")

    # Отдельный индекс во временном файле: настоящий индекс и рабочее
    # дерево остаются ровно такими, какими были.
    handle, index = tempfile.mkstemp(prefix="kmarket-index-")
    os.close(handle)
    os.remove(index)  # git сам создаст индекс; пустой файл он считает битым
    env = {**os.environ, "GIT_INDEX_FILE": index}
    try:
        _git("read-tree", base, env=env)
        for path, content in files.items():
            blob = _git("hash-object", "-w", "--stdin", data=content.encode("utf-8"))
            _git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        tree = _git("write-tree", env=env)
    finally:
        try:
            os.remove(index)
        except OSError:
            pass

    if tree == _git("rev-parse", f"{base}^{{tree}}"):
        return base  # в облаке уже ровно это — коммитить нечего
    return _git("commit-tree", tree, "-p", base, "-m", message)


def _write_local(files: dict[str, str]) -> None:
    for path, content in files.items():
        target = config.ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(target)


def publish(files: dict[str, str], message: str, attempts: int = 3) -> str:
    """Отправить файлы в облако одним коммитом. Возвращает текст для человека.

    `files` — {путь от корня репозитория: новое содержимое}. Бросает
    RuntimeError, если отправить не удалось: молча проглотить такую ошибку
    нельзя, иначе человек будет думать, что товар пишется, а облако о нём
    не знает.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    full = f"{message} ({stamp} UTC)"
    last_error = ""
    for _ in range(attempts):
        commit = _commit_on_origin(files, full)
        try:
            _git("push", "-q", "origin", f"{commit}:refs/heads/main")
            break
        except RuntimeError as error:
            # Почти всегда — облачный сборщик успел запушить раньше нас.
            # Собираем коммит заново поверх его свежего.
            last_error = str(error)
    else:
        raise RuntimeError(f"не удалось отправить в облако: {last_error}")

    # Подтянуть свой же коммит в локальную копию. Рабочие версии наших
    # файлов сначала откатываем к HEAD: иначе fast-forward откажется,
    # увидев «локальные правки», хотя они ровно те, что мы отправили.
    _git("fetch", "-q", "origin", "main")
    try:
        for path in files:
            try:
                _git("cat-file", "-e", f"HEAD:{path}")
            except RuntimeError:
                # Файла в HEAD ещё нет — убираем локальный, иначе git
                # откажется затирать «неотслеживаемый» файл.
                (config.ROOT / path).unlink(missing_ok=True)
                continue
            _git("checkout", "HEAD", "--", path)
        _git("merge", "-q", "--ff-only", "origin/main")
    except RuntimeError:
        # Локальная ветка ушла вбок (чьи-то неотправленные коммиты). Облако
        # уже знает правду; локально просто кладём файлы на место.
        _write_local(files)
        return "отправлено в облако; локальная копия подтянется при следующем запуске"
    return "отправлено в облако"
