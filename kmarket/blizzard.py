"""Клиент Blizzard Game Data API — ровно то, что нужно сборщику.

Ноль зависимостей (только стандартная библиотека): сборщик крутится в GitHub
Actions каждые 10 минут, и `pip install` там — это лишняя минута на каждый
запуск и лишняя точка отказа.

ГЛАВНЫЙ ФАКТ ПРО ЭТОТ API: /data/wow/token/index отдаёт ТОЛЬКО текущую цену
и время последнего обновления. Истории у Blizzard нет вообще. Вся аналитика
KMARKET стоит на том, что историю мы копим сами — поэтому сборщик важнее
дашборда, и пропущенные часы не восстанавливаются ничем.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from . import __version__, config

OAUTH_URL = "https://oauth.battle.net/token"
USER_AGENT = f"KMARKET/{__version__} (KareninTeam; WoW Token tracker)"
TIMEOUT = 30
COPPER_PER_GOLD = 10_000


@dataclass(frozen=True)
class TokenPrice:
    """Один замер цены жетона."""

    region: str
    updated: datetime  # момент, которым Blizzard датирует цену (UTC)
    price_copper: int

    @property
    def gold(self) -> int:
        return self.price_copper // COPPER_PER_GOLD


def _http_json(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    attempts: int = 3,
) -> dict:
    """GET/POST с ретраями. Сетевой сбой не должен ронять сбор целиком."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})}
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(2**attempt)  # 2с, 4с — Blizzard иногда моргает
    raise RuntimeError(f"Запрос к {url} не удался после {attempts} попыток: {last_error}")


def get_access_token() -> str:
    """OAuth client credentials. Токен живёт сутки, но мы берём свежий на запуск."""
    pair = f"{config.require('BLIZZARD_CLIENT_ID')}:{config.require('BLIZZARD_CLIENT_SECRET')}"
    basic = base64.b64encode(pair.encode("ascii")).decode("ascii")
    payload = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("ascii")
    data = _http_json(
        OAUTH_URL,
        data=payload,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    return data["access_token"]


def fetch_token_price(region: str, access_token: str) -> TokenPrice:
    """Текущая цена жетона в регионе."""
    url = (
        f"https://{region}.api.blizzard.com/data/wow/token/index"
        f"?namespace=dynamic-{region}"
    )
    data = _http_json(url, headers={"Authorization": f"Bearer {access_token}"})
    return TokenPrice(
        region=region,
        # last_updated_timestamp приходит в МИЛЛИсекундах
        updated=datetime.fromtimestamp(data["last_updated_timestamp"] / 1000, tz=timezone.utc),
        price_copper=int(data["price"]),
    )


def fetch_all(regions: tuple[str, ...] = config.REGIONS) -> list[TokenPrice]:
    """Цены по всем регионам одним access-токеном."""
    access_token = get_access_token()
    return [fetch_token_price(region, access_token) for region in regions]
