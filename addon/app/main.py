"""FastAPI application: web UI + JSON API, with the trip tracker as a background task."""
from __future__ import annotations

import asyncio
import csv
import io
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from .classify import classify
from .config import Config
from .ha_client import HAClient
from .importer import parse_workbook, ParsedTrip
from .storage import (
    BUSINESS,
    PRIVATE,
    SOURCE_AUTO,
    SOURCE_IMPORT,
    SOURCE_MANUAL,
    Storage,
    Trip,
)
from .tracker import TripTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
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


# ------------------------------------------------------------ middleware
@app.middleware("http")
async def ingress_root_path(request: Request, call_next):
    """Honor Home Assistant ingress path prefix."""
    ingress_path = request.headers.get("X-Ingress-Path")
    if ingress_path:
        request.scope["root_path"] = ingress_path
    return await call_next(request)


# ------------------------------------------------------------ helpers
def _summary(storage: Storage, config: Config, year: int) -> dict:
    private_km = storage.private_km_total(year)
    business_km = storage.business_km_total(year)
    budget = config.private_km_budget
    private_by_plate = storage.km_by_plate(year, PRIVATE)
    return {
        "year": year,
        "private_km": round(private_km, 1),
        "business_km": round(business_km, 1),
        "budget": budget,
        "remaining": round(budget - private_km, 1),
        "pct": round(min(private_km / budget * 100, 100), 1) if budget else 0,
        "over_budget": private_km > budget,
        "private_by_plate": private_by_plate,
    }


# ------------------------------------------------------------- JSON API
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


@app.delete("/api/trips/{trip_id}")
def api_delete_trip(trip_id: int):
    storage: Storage = app.state.storage
    if not storage.delete_trip(trip_id):
        raise HTTPException(status_code=404, detail="Trip not found")
    return {"deleted": trip_id}


@app.post("/api/trips")
def api_create_manual_trip(payload: dict):
    """Create a manual trip via API."""
    storage: Storage = app.state.storage
    config: Config = app.state.config
    trip = _manual_trip_from_payload(payload, config)
    tid = storage.create_trip(trip)
    return asdict(storage.get_trip(tid))


@app.post("/api/import")
async def api_import(file: UploadFile = File(...)):
    """Parse an uploaded Excel workbook and return the preview (no DB write)."""
    content = await file.read()
    result = parse_workbook(content)
    return {
        "plate": result.plate,
        "year": result.year,
        "trips": [t.to_dict() for t in result.trips],
        "warnings": result.warnings,
        "skipped": result.skipped,
    }


@app.post("/api/import/confirm")
async def api_import_confirm(file: UploadFile = File(...)):
    """Parse and store all valid rows from an uploaded workbook."""
    storage: Storage = app.state.storage
    content = await file.read()
    result = parse_workbook(content)
    inserted, dupes = _store_import(storage, result.trips)
    return {
        "inserted": inserted,
        "duplicates": dupes,
        "skipped": result.skipped,
        "warnings": result.warnings,
    }


def _apply_update(storage: Storage, trip_id: int, payload: dict) -> None:
    fields: dict = {}
    if "classification" in payload:
        value = payload["classification"]
        if value not in (BUSINESS, PRIVATE):
            raise HTTPException(status_code=400, detail="Invalid classification")
        fields["classification"] = value
        fields["overridden"] = 1
    for key in ("km", "note", "start_zone", "end_zone", "start_city", "end_city", "license_plate"):
        if key in payload:
            fields[key] = payload[key]
    fields["updated_at"] = datetime.utcnow().isoformat()
    storage.update_trip(trip_id, **fields)


def _manual_trip_from_payload(payload: dict, config: Config) -> Trip:
    now = datetime.utcnow().isoformat()
    classification = payload.get("classification", PRIVATE)
    if classification not in (BUSINESS, PRIVATE):
        raise HTTPException(status_code=400, detail="Invalid classification")
    return Trip(
        start_time=payload.get("date", now[:10]) + "T00:00:00",
        end_time=payload.get("date", now[:10]) + "T23:59:59",
        km=float(payload["km"]) if "km" in payload else None,
        start_city=payload.get("start_city", ""),
        end_city=payload.get("end_city", ""),
        license_plate=payload.get("license_plate", config.license_plate),
        source=SOURCE_MANUAL,
        classification=classification,
        auto_classification=classification,
        overridden=1,
        note=payload.get("note"),
        created_at=now,
        updated_at=now,
    )


def _store_import(storage: Storage, trips: list[ParsedTrip]) -> tuple[int, int]:
    """Insert parsed trips into the DB, deduplicating against previously-imported rows.

    Dedup is keyed on date+km+plate+cities+note. Rows that are genuinely identical
    *within a single file* (the same trip logged twice in the source) are both kept —
    we only skip a row that already existed in the DB *before this import run*, so
    re-importing the same file is idempotent without silently dropping legitimate
    same-day repeat trips.
    """
    inserted, dupes = 0, 0
    now = datetime.utcnow().isoformat()
    # Snapshot which (key, count) pairs already exist so we can allow in-file repeats.
    seen_this_run: dict[tuple, int] = {}
    for t in trips:
        key = (t.date, t.km, t.license_plate, t.start_city, t.end_city, t.note)
        already = storage.import_exists(
            t.date, t.km, t.license_plate, t.start_city, t.end_city, t.note
        )
        # If the row exists in DB and we haven't yet re-inserted it this run, it's a dupe.
        if already and seen_this_run.get(key, 0) == 0:
            dupes += 1
            continue
        trip = Trip(
            start_time=f"{t.date}T00:00:00",
            end_time=f"{t.date}T23:59:59",
            start_odo=t.start_odo,
            end_odo=t.end_odo,
            km=t.km,
            start_city=t.start_city,
            end_city=t.end_city,
            license_plate=t.license_plate,
            source=SOURCE_IMPORT,
            classification=t.classification,
            auto_classification=t.classification,
            overridden=0,
            note=t.note,
            created_at=now,
            updated_at=now,
        )
        storage.create_trip(trip)
        seen_this_run[key] = seen_this_run.get(key, 0) + 1
        inserted += 1
    return inserted, dupes


# ------------------------------------------------------------------- export
# Columns for the tax-year export, in order. (header, Trip attribute).
_EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("Datum", "date"),
    ("Kenteken", "license_plate"),
    ("Van", "start_city"),
    ("Naar", "end_city"),
    ("Km", "km"),
    ("Type", "type_nl"),
    ("Bron", "source"),
    ("Handmatig aangepast", "overridden_nl"),
    ("Notitie", "note"),
]


def _export_rows(trips: list[Trip]) -> list[list]:
    """Flatten trips into export rows (ascending by date for a readable logbook)."""
    rows: list[list] = []
    for t in sorted(trips, key=lambda x: x.start_time or ""):
        values = {
            "date": (t.start_time or "")[:10],
            "license_plate": t.license_plate or "",
            "start_city": t.start_city or t.start_zone or "",
            "end_city": t.end_city or t.end_zone or "",
            "km": round(t.km, 1) if t.km is not None else "",
            "type_nl": "Zakelijk" if t.classification == BUSINESS else "Privé",
            "source": t.source,
            "overridden_nl": "ja" if t.overridden else "",
            "note": t.note or "",
        }
        rows.append([values[attr] for _, attr in _EXPORT_COLUMNS])
    return rows


def _export_filename(year: int, ext: str) -> str:
    return f"kilometerregistratie-{year}.{ext}"


@app.get("/export.csv")
@app.get("/api/export.csv")
def export_csv(year: Optional[int] = None):
    """Download the full trip log for a tax year as CSV."""
    storage: Storage = app.state.storage
    year = year or datetime.now().year
    trips = storage.list_trips(year=year)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")  # ; for Excel-NL friendliness
    writer.writerow([header for header, _ in _EXPORT_COLUMNS])
    writer.writerows(_export_rows(trips))
    data = buf.getvalue().encode("utf-8-sig")  # BOM so Excel detects UTF-8
    return Response(
        content=data,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{_export_filename(year, "csv")}"'
        },
    )


@app.get("/export.xlsx")
@app.get("/api/export.xlsx")
def export_xlsx(year: Optional[int] = None):
    """Download the full trip log for a tax year as an Excel workbook."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    storage: Storage = app.state.storage
    config: Config = app.state.config
    year = year or datetime.now().year
    trips = storage.list_trips(year=year)
    summary = _summary(storage, config, year)

    wb = Workbook()
    ws = wb.active
    ws.title = f"Ritten {year}"

    headers = [header for header, _ in _EXPORT_COLUMNS]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for row in _export_rows(trips):
        ws.append(row)

    # Totals + budget footer.
    ws.append([])
    ws.append(["Totaal zakelijk (km)", summary["business_km"]])
    ws.append(["Totaal privé (km)", summary["private_km"]])
    ws.append([f"Privé-budget (km)", summary["budget"]])
    ws.append(["Privé over (km)", summary["remaining"]])
    for row in ws.iter_rows(min_row=ws.max_row - 3, max_row=ws.max_row, min_col=1, max_col=1):
        for cell in row:
            cell.font = Font(bold=True)

    # Reasonable column widths.
    widths = [12, 12, 18, 18, 8, 10, 8, 18, 30]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{_export_filename(year, "xlsx")}"'
        },
    )


# ------------------------------------------------------------------- Web UI
@app.get("/", response_class=HTMLResponse)
def index(request: Request, year: Optional[int] = None, plate: Optional[str] = None):
    storage: Storage = app.state.storage
    config: Config = app.state.config
    year = year or datetime.now().year
    # Normalise an empty/"all" selection to "no filter".
    plate = plate or None
    plates = storage.plates(year=year)
    # Guard against a stale plate query from another year.
    selected_plate = plate if plate in plates else None
    trips = storage.list_trips(year=year, plate=selected_plate)
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
            "plates": plates,
            "selected_plate": selected_plate,
            "BUSINESS": BUSINESS,
            "PRIVATE": PRIVATE,
            "root_path": request.scope.get("root_path", ""),
            "work_zones": config.work_zones,
            "license_plate": config.license_plate,
            "today": datetime.now().strftime("%Y-%m-%d"),
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
    date: Optional[str] = Form(None),
    km: Optional[float] = Form(None),
    start_city: Optional[str] = Form(None),
    end_city: Optional[str] = Form(None),
    license_plate: Optional[str] = Form(None),
    classification: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
):
    """Save an inline full-row edit. Only fields that were sent are updated."""
    storage: Storage = app.state.storage
    trip = storage.get_trip(trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")

    payload: dict = {}
    if km is not None:
        payload["km"] = km
    if start_city is not None:
        payload["start_city"] = start_city
    if end_city is not None:
        payload["end_city"] = end_city
    if license_plate is not None:
        payload["license_plate"] = license_plate
    if note is not None:
        payload["note"] = note
    # Classification goes through _apply_update's validation (sets overridden=1).
    if classification is not None:
        payload["classification"] = classification
    if payload:
        _apply_update(storage, trip_id, payload)

    # Date edits shift start/end_time while preserving the time-of-day parts.
    if date:
        start_time = f"{date}T{(trip.start_time or '')[11:] or '00:00:00'}"
        end_time = (
            f"{date}T{(trip.end_time or '')[11:] or '23:59:59'}"
            if trip.end_time
            else trip.end_time
        )
        storage.update_trip(
            trip_id,
            start_time=start_time,
            end_time=end_time,
            updated_at=datetime.utcnow().isoformat(),
        )

    base = request.scope.get("root_path", "")
    return RedirectResponse(url=f"{base}/?year={year}", status_code=303)


@app.post("/trips/{trip_id}/delete")
def ui_delete(request: Request, trip_id: int, year: int = Form(...)):
    storage: Storage = app.state.storage
    storage.delete_trip(trip_id)
    base = request.scope.get("root_path", "")
    return RedirectResponse(url=f"{base}/?year={year}", status_code=303)


@app.post("/trips/manual")
def ui_add_manual(
    request: Request,
    year: int = Form(...),
    date: str = Form(...),
    km: float = Form(...),
    start_city: str = Form(""),
    end_city: str = Form(""),
    classification: str = Form(PRIVATE),
    license_plate: str = Form(""),
    note: str = Form(""),
):
    storage: Storage = app.state.storage
    config: Config = app.state.config
    trip = _manual_trip_from_payload(
        {
            "date": date,
            "km": km,
            "start_city": start_city,
            "end_city": end_city,
            "classification": classification,
            "license_plate": license_plate or config.license_plate,
            "note": note or None,
        },
        config,
    )
    storage.create_trip(trip)
    base = request.scope.get("root_path", "")
    return RedirectResponse(url=f"{base}/?year={year}", status_code=303)


@app.post("/import")
async def ui_import(request: Request, file: UploadFile = File(...)):
    """Upload an Excel workbook and import all valid trips."""
    storage: Storage = app.state.storage
    content = await file.read()
    result = parse_workbook(content)
    inserted, dupes = _store_import(storage, result.trips)
    # Find the right year to redirect to.
    year = result.year or datetime.now().year
    base = request.scope.get("root_path", "")
    return RedirectResponse(url=f"{base}/?year={year}", status_code=303)
