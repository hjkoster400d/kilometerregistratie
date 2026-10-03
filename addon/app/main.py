"""FastAPI application: web UI + JSON API, with the trip tracker as a background task."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .classify import classify
from .config import Config
from .ha_client import HAClient
from .storage import BUSINESS, PRIVATE, Storage
from .tracker import TripTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kilometerregistratie")

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = Config.load()
    storage = Storage(config.db_path)
    ha = HAClient(config)
    tracker = TripTracker(config, ha, storage)

    app.state.config = config
    app.state.storage = storage
    app.state.ha = ha

    task = asyncio.create_task(tracker.run())
    log.info("Started trip tracker background task")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        storage.close()


app = FastAPI(title="Kilometerregistratie", lifespan=lifespan)
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@app.middleware("http")
async def ingress_root_path(request: Request, call_next):
    """Honor Home Assistant ingress.

    HA's Supervisor proxies the add-on under a dynamic path prefix and sends it in
    the `X-Ingress-Path` header. Setting root_path makes FastAPI/Starlette generate
    correct URLs (via url_for) and lets relative links resolve under that prefix.
    """
    ingress_path = request.headers.get("X-Ingress-Path")
    if ingress_path:
        request.scope["root_path"] = ingress_path
    return await call_next(request)


def _summary(storage: Storage, config: Config, year: int) -> dict:
    private_km = storage.private_km_total(year)
    business_km = storage.business_km_total(year)
    budget = config.private_km_budget
    return {
        "year": year,
        "private_km": round(private_km, 1),
        "business_km": round(business_km, 1),
        "budget": budget,
        "remaining": round(budget - private_km, 1),
        "pct": round(min(private_km / budget * 100, 100), 1) if budget else 0,
        "over_budget": private_km > budget,
    }


# ----------------------------------------------------------------- JSON API
@app.get("/api/trips")
def api_trips(year: Optional[int] = None, classification: Optional[str] = None):
    storage: Storage = app.state.storage
    trips = storage.list_trips(year=year, classification=classification)
    return [asdict(t) for t in trips]


@app.get("/api/summary")
def api_summary(year: Optional[int] = None):
    storage: Storage = app.state.storage
    config: Config = app.state.config
    year = year or datetime.now().year
    return _summary(storage, config, year)


@app.patch("/api/trips/{trip_id}")
def api_update_trip(trip_id: int, payload: dict):
    storage: Storage = app.state.storage
    trip = storage.get_trip(trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    _apply_update(storage, trip_id, payload)
    return asdict(storage.get_trip(trip_id))


def _apply_update(storage: Storage, trip_id: int, payload: dict) -> None:
    fields: dict = {}
    if "classification" in payload:
        value = payload["classification"]
        if value not in (BUSINESS, PRIVATE):
            raise HTTPException(status_code=400, detail="Invalid classification")
        fields["classification"] = value
        fields["overridden"] = 1
    for key in ("km", "note", "start_zone", "end_zone"):
        if key in payload:
            fields[key] = payload[key]
    fields["updated_at"] = datetime.utcnow().isoformat()
    storage.update_trip(trip_id, **fields)


# ------------------------------------------------------------------- Web UI
@app.get("/", response_class=HTMLResponse)
def index(request: Request, year: Optional[int] = None):
    storage: Storage = app.state.storage
    config: Config = app.state.config
    year = year or datetime.now().year
    trips = storage.list_trips(year=year)
    summary = _summary(storage, config, year)
    years = sorted(
        {int(t.start_time[:4]) for t in storage.list_trips() if t.start_time},
        reverse=True,
    ) or [year]
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "trips": trips,
            "summary": summary,
            "year": year,
            "years": years,
            "BUSINESS": BUSINESS,
            "PRIVATE": PRIVATE,
            "root_path": request.scope.get("root_path", ""),
            "work_zones": config.work_zones,
        },
    )


@app.post("/trips/{trip_id}/classify")
def ui_classify(
    request: Request,
    trip_id: int,
    classification: str = Form(...),
    year: int = Form(...),
):
    storage: Storage = app.state.storage
    _apply_update(storage, trip_id, {"classification": classification})
    base = request.scope.get("root_path", "")
    return RedirectResponse(url=f"{base}/?year={year}", status_code=303)


@app.post("/trips/{trip_id}/edit")
def ui_edit(
    request: Request,
    trip_id: int,
    year: int = Form(...),
    km: Optional[float] = Form(None),
    note: Optional[str] = Form(None),
):
    storage: Storage = app.state.storage
    payload: dict = {}
    if km is not None:
        payload["km"] = km
    if note is not None:
        payload["note"] = note
    if payload:
        _apply_update(storage, trip_id, payload)
    base = request.scope.get("root_path", "")
    return RedirectResponse(url=f"{base}/?year={year}", status_code=303)
