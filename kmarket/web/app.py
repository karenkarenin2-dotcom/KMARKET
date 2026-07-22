"""FastAPI-приложение дашборда.

Тонкая оболочка: вся математика живёт в kmarket.analytics, здесь только
маршруты и отдача статики. Слушает localhost — это личный инструмент,
наружу его никто не выставляет.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .. import config
from ..analytics import report as build_report
from ..analytics.report import chart_data

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
TEMPLATES = HERE / "templates"

app = FastAPI(title="KMARKET", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _check_region(region: str) -> str:
    if region not in config.REGIONS:
        raise HTTPException(404, f"Неизвестный регион: {region}")
    return region


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (TEMPLATES / "index.html").read_text(encoding="utf-8")


@app.get("/api/report/{region}")
def api_report(region: str, fresh: bool = False) -> dict:
    return build_report(_check_region(region), fresh=fresh)


@app.get("/api/chart/{region}")
def api_chart(region: str, days: int = 90) -> dict:
    _check_region(region)
    days = max(1, min(days, 100_000))
    return {"region": region, "days": days, **chart_data(region, days)}
