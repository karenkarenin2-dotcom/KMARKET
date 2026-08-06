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
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import __version__, config

OAUTH_URL = "https://oauth.battle.net/token"
USER_AGENT = f"KMARKET/{__version__} (KareninTeam; WoW market tracker)"
TIMEOUT = 30
# Снимок аукциона — это десятки мегабайт, ему мало тридцати секунд.
BIG_TIMEOUT = 180
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


def _http_raw(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    attempts: int = 3,
    timeout: int = TIMEOUT,
) -> tuple[bytes, dict[str, str]]:
    """GET/POST с ретраями. Возвращает (тело, заголовки).

    ДВЕ ГРАБЛИ ПРО GZIP, обе проверены на живом ответе 2026-08-06.

    Первая: urllib НЕ шлёт `Accept-Encoding: gzip` сам. Для жетона это
    безразлично, но снимок аукциона без сжатия весит 24.8 МБ против 2.9 МБ
    сжатого — восьмикратная разница на каждом часовом запуске.

    Вторая: попросив gzip, распаковывать придётся ТОЖЕ САМОМУ — urllib
    отдаёт тело как есть. Причём Blizzard кладёт заголовок в нижнем
    регистре (`content-encoding`), а `http.client` складывает заголовки в
    регистронезависимый словарь, но `dict(response.headers)` эту
    любезность теряет. Поэтому опознаём сжатие не по заголовку, а по
    сигнатуре `1f 8b` в первых двух байтах — она не врёт никогда.

    Ретраи обязательны: самый первый же наш запрос к commodities вернул
    случайный HTTP 500, повтор через две секунды прошёл нормально.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "gzip",
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                meta = {key.lower(): value for key, value in response.headers.items()}
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            return body, meta
        except urllib.error.HTTPError as error:
            # 4xx — это ответ, а не сбой: повторять его бессмысленно.
            # Особенно важно для 404: справочник намеренно пробует
            # несуществующие соседние ID, и с ретраями каждая такая проба
            # стоила бы 6 секунд сна на ровном месте (2с + 4с). Один раз
            # это уже подвесило сборку списка слежки.
            # 429 — исключение: «слишком часто» лечится именно паузой.
            if 400 <= error.code < 500 and error.code != 429:
                raise
            last_error = error
            if attempt < attempts:
                time.sleep(2**attempt)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(2**attempt)  # 2с, 4с — Blizzard иногда моргает
    raise RuntimeError(f"Запрос к {url} не удался после {attempts} попыток: {last_error}")


def _http_json(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    attempts: int = 3,
    timeout: int = TIMEOUT,
) -> dict:
    body, _ = _http_raw(
        url, data=data, headers=headers, attempts=attempts, timeout=timeout
    )
    return json.loads(body.decode("utf-8"))


def _http_json_dated(
    url: str, *, headers: dict[str, str] | None = None, timeout: int = TIMEOUT
) -> tuple[dict, datetime | None]:
    """То же, но ещё и время из `Last-Modified` — им дедуплицируем снимки.

    Для аукциона это ровно то же решение, что `updated_utc` для жетона:
    ключом служит время САМОЙ Blizzard, а не время нашего опроса. Иначе
    один и тот же часовой снимок, пойманный двумя запусками, лёг бы в
    историю двумя разными точками.
    """
    body, meta = _http_raw(url, headers=headers, timeout=timeout)
    stamp = meta.get("last-modified")
    moment: datetime | None = None
    if stamp:
        try:
            # Свой разбор писать нельзя: Blizzard шлёт день без ведущего
            # нуля («Thu, 6 Aug 2026 20:41:27 GMT»). parsedate_to_datetime —
            # штатный разборщик RFC 2822, ему такое привычно.
            moment = parsedate_to_datetime(stamp).astimezone(timezone.utc)
        except (TypeError, ValueError):
            moment = None
    return json.loads(body.decode("utf-8")), moment


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
