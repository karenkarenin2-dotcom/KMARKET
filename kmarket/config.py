"""Конфигурация KMARKET.

Секреты живут ТОЛЬКО в окружении (GitHub Secrets) или в локальном .env,
который не коммитится. В коде их нет и быть не может.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"  # замеры облачного сборщика (в git)
ARCHIVE_DIR = DATA_DIR / "archive"  # разовый бутстрап из внешнего источника
# Замеры, снятые локальным дашбордом. НЕ в git (см. .gitignore): у git-истории
# один владелец — облачный сборщик. Смешивание двух писателей в одном файле
# 2026-07-26 привело к маркерам конфликта внутри данных, см. storage.append_live.
LIVE_DIR = DATA_DIR / "live"
# История товарного аукциона. Владелец — облачный сборщик, как и у history:
# дашборд сюда не пишет НИКОГДА (правило одного писателя, оплаченное
# поломкой 2026-07-26).
AUCTION_DIR = DATA_DIR / "auction"

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
    """Подтягивает .env в os.environ. ФАЙЛ ПОБЕЖДАЕТ окружение.

    Свой парсер вместо python-dotenv: сборщик обязан работать на голом
    Python без единой зависимости.

    ПОЧЕМУ ФАЙЛ ГЛАВНЕЕ (баг, пойманный 2026-07-23, стоил трёх сообщений,
    ушедших чужому боту). Раньше здесь стоял `os.environ.setdefault`, то
    есть системная переменная побеждала .env. У Карена в Windows оказалась
    User-переменная TELEGRAM_BOT_TOKEN от совсем другого проекта
    (бот-напоминалка о днях рождения) — и KMARKET молча слал алерты в чужой
    чат. Токен из .env при этом был правильный, и по логам всё выглядело
    исправным: sendMessage возвращал ok=true.

    .env — это ЯВНАЯ конфигурация ЭТОГО проекта, а окружение — глобальная
    свалка, куда что угодно мог положить любой другой проект. Поэтому файл
    главнее. В CI .env не существует (он в .gitignore), там работают
    GitHub Secrets через окружение — этот случай не затронут.

    Расхождение не проглатываем молча: печатаем предупреждение, иначе
    следующая такая коллизия снова будет искаться часами.
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
            raw_key, _, raw_value = line.partition("=")
            key = raw_key.strip()
            value = raw_value.strip().strip("\"'")
            existing = os.environ.get(key)
            if existing is not None and existing != value:
                print(
                    f"[KMARKET] Внимание: переменная окружения {key} отличается от .env — "
                    f"беру значение из .env (файл проекта главнее глобальной переменной)."
                )
            os.environ[key] = value
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
