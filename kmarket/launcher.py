"""Запуск приложения: python -m kmarket

Проверяет зависимости, подтягивает свежую историю из облака и открывает
окно. `launch/KMARKET.bat` зовёт ровно это.

ВЕСЬ ТЕКСТ ДЛЯ ЧЕЛОВЕКА ЖИВЁТ ЗДЕСЬ, А НЕ В .BAT. Батник cmd.exe читает в
кодировке консоли (на русской Windows это cp866), поэтому кириллица в нём
превращается в мусор и ломает разбор файла — куски комментариев улетают в
интерпретатор как команды. `chcp 65001` не спасает, проверено на KTRANS.
Python пишет в консоль через Unicode-API и от кодовой страницы не зависит.
"""

from __future__ import annotations

import subprocess
import sys

from . import config


def _sync_history() -> None:
    """Подтянуть свежую историю из облака перед стартом.

    Сборщик пишет данные в GitHub, приложение читает их с диска — без
    этого оно показывало бы историю на момент последнего pull. Проверено
    на живой машине: локальная копия отставала на 122 коммита, и цена
    выглядела шестидневной давности при исправном сборщике.

    ТОЛЬКО --ff-only, НИКОГДА --rebase --autostash (урок 2026-07-26).
    Прежний вариант с автозаначкой при неудачном возврате вписывал
    маркеры конфликта ПРЯМО В ФАЙЛЫ ДАННЫХ, ломал историю и оставлял git
    в состоянии `UU`. Запуск обязан быть безопасной операцией:
    fast-forward либо ничего не делает, либо доматывает коммиты — он
    физически не может испортить рабочие файлы.
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


def _check_deps() -> bool:
    missing = []
    for module, package in (("webview", "pywebview"), ("pandas", "pandas")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        print("[KMARKET] Не хватает библиотек:", ", ".join(missing))
        print("[KMARKET] Поставь их одной командой:")
        print("[KMARKET]     pip install -r requirements.txt")
        return False
    return True


def main() -> int:
    print("[KMARKET] KMARKET by KareninTeam")
    if not _check_deps():
        return 1

    _sync_history()
    print("[KMARKET] Первый расчёт занимает несколько секунд — считаются события и бэктест.")

    from .app import main as run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
