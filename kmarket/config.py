"""Конфигурация KMARKET.

Секреты живут ТОЛЬКО в окружении (GitHub Secrets) или в локальном .env,
который не коммитится. В коде их нет и быть не может.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"  # наши собственные замеры
ARCHIVE_DIR = DATA_DIR / "archive"  # разовый бутстрап из внешнего источника

# EU — основной регион. US собираем как ГИПОТЕЗУ об опережающем индикаторе:
# часовые пояса сдвинуты, и если US-движение предсказывает EU — это сигнал,
# которого нет у публичных трекеров. Гипотеза проверяется в аналитике.
REGIONS = ("eu", "us")
PRIMARY_REGION = "eu"

# Данные ВСЕГДА хранятся в UTC. Показываем в двух поясах (решение Карена):
LOCAL_TZ = "Asia/Omsk"  # график цены — в твоём времени (UTC+6)
SERVER_TZ = "Europe/Paris"  # сезонность — по времени EU-серверов (CET/CEST)

_DOTENV_LOADED = False


def load_dotenv(path: Path | None = None) -> None:
    """Подтягивает .env в os.environ, НЕ затирая уже заданные переменные.

    Свой парсер вместо python-dotenv: сборщик обязан работать на голом
    Python без единой зависимости.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED and path is None:
        return
    path = path or ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    _DOTENV_LOADED = True


def require(name: str) -> str:
    """Обязательная переменная. Внятно ругается, если её нет."""
    load_dotenv()
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Не задана переменная {name}.\n"
            f"  Локально — положи её в .env (шаблон: .env.example).\n"
            f"  В CI — в Settings → Secrets and variables → Actions."
        )
    return value


def optional(name: str, default: str = "") -> str:
    """Необязательная переменная (алерты работают, только если она есть)."""
    load_dotenv()
    return os.environ.get(name, default).strip()
