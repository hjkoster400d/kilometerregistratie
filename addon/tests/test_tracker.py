"""Unit tests for classification, transition detection, storage, and trip flow."""
import asyncio

import pytest

from app.classify import classify
from app.storage import BUSINESS, PRIVATE, Storage, Trip
from app.tracker import TripTracker, detect_transition, TRIP_START, TRIP_END

WORK = ["zone.werk"]


# ----------------------------------------------------------- classification
@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("zone.home", "zone.werk", BUSINESS),   # home -> work
        ("zone.werk", "zone.home", BUSINESS),   # work -> home
        ("zone.werk", "zone.werk", BUSINESS),   # within work
        ("zone.home", "zone.de_bron", PRIVATE), # private
        (None, None, PRIVATE),                  # unknown both ends
        ("zone.home", None, PRIVATE),           # unknown end, non-work start
        (None, "zone.werk", BUSINESS),          # unknown start, work end
    ],
)
def test_classify(start, end, expected):
    assert classify(start, end, WORK) == expected


# -------------------------------------------------------- transition detect
@pytest.mark.parametrize(
    "old,new,expected",
    [
        ("0", "4", TRIP_START),
        ("0", "2", TRIP_START),
        ("off", "1", TRIP_START),
        ("4", "0", TRIP_END),
        ("2", "off", TRIP_END),
        ("0", "0", None),
        ("4", "2", None),       # on -> on, no boundary
        (None, "4", TRIP_START),
        ("unknown", "4", TRIP_START),
        ("4", "unavailable", TRIP_END),
    ],
)
def test_detect_transition(old, new, expected):
    assert detect_transition(old, new) == expected


# ------------------------------------------------------------------ storage
def test_storage_private_budget():
    s = Storage(":memory:")
    s.create_trip(Trip(start_time="2026-01-01T08:00:00", km=10, classification=PRIVATE,
                        created_at="x", updated_at="x"))
    s.create_trip(Trip(start_time="2026-02-01T08:00:00", km=5, classification=PRIVATE,
                        created_at="x", updated_at="x"))
    s.create_trip(Trip(start_time="2026-02-01T09:00:00", km=40, classification=BUSINESS,
                        created_at="x", updated_at="x"))
    s.create_trip(Trip(start_time="2025-02-01T09:00:00", km=99, classification=PRIVATE,
                        created_at="x", updated_at="x"))
    assert s.private_km_total(2026) == 15
    assert s.business_km_total(2026) == 40
    assert s.private_km_total(2025) == 99


def test_storage_override_wins():
    s = Storage(":memory:")
    tid = s.create_trip(Trip(start_time="2026-01-01T08:00:00", km=10,
                             classification=BUSINESS, auto_classification=BUSINESS,
                             created_at="x", updated_at="x"))
    s.update_trip(tid, classification=PRIVATE, overridden=1)
    trip = s.get_trip(tid)
    assert trip.classification == PRIVATE
    assert trip.auto_classification == BUSINESS
    assert trip.overridden == 1


# ---------------------------------------------------------- end-to-end flow
class FakeConfig:
    work_zones = WORK
    min_trip_km = 0.5
    ignition_entity = "sensor.ign"
    license_plate = "TEST-01"


class FakeHA:
    """Scripted HA client: returns queued odometer/location snapshots."""
    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self._i = 0

    async def get_odometer(self):
        return self._snapshots[self._i]["odo"]

    async def get_location(self):
        snap = self._snapshots[self._i]
        self._i += 1
        return {"lat": snap.get("lat"), "lon": snap.get("lon"), "zone": snap["zone"]}


def _event(old, new):
    return {"old_state": {"state": old}, "new_state": {"state": new}}


def test_business_trip_home_to_work():
    s = Storage(":memory:")
    ha = FakeHA([
        {"odo": 100.0, "zone": "zone.home"},   # start
        {"odo": 142.0, "zone": "zone.werk"},   # end
    ])
    tracker = TripTracker(FakeConfig(), ha, s)
    asyncio.run(tracker.handle_event(_event("0", "4")))
    asyncio.run(tracker.handle_event(_event("4", "0")))
    trips = s.list_trips()
    assert len(trips) == 1
    assert trips[0].km == 42.0
    assert trips[0].classification == BUSINESS
    assert trips[0].start_zone == "zone.home"
    assert trips[0].end_zone == "zone.werk"


def test_private_trip_discarded_as_noise():
    s = Storage(":memory:")
    ha = FakeHA([
        {"odo": 100.0, "zone": "zone.home"},
        {"odo": 100.2, "zone": "zone.home"},   # 0.2 km < MIN_TRIP_KM
    ])
    tracker = TripTracker(FakeConfig(), ha, s)
    asyncio.run(tracker.handle_event(_event("0", "4")))
    asyncio.run(tracker.handle_event(_event("4", "0")))
    trip = s.list_trips()[0]
    assert trip.note and "discarded" in trip.note
    # discarded trips are not counted toward either total
    assert s.private_km_total(trip.start_time[:4] and int(trip.start_time[:4])) == 0.0


def test_private_trip_counts_to_budget():
    s = Storage(":memory:")
    ha = FakeHA([
        {"odo": 200.0, "zone": "zone.home"},
        {"odo": 230.0, "zone": "zone.de_bron"},  # neither endpoint is work
    ])
    tracker = TripTracker(FakeConfig(), ha, s)
    asyncio.run(tracker.handle_event(_event("0", "4")))
    asyncio.run(tracker.handle_event(_event("4", "0")))
    trip = s.list_trips()[0]
    assert trip.classification == PRIVATE
    assert trip.km == 30.0


def test_auto_trip_records_plate_and_cities():
    s = Storage(":memory:")
    ha = FakeHA([
        {"odo": 100.0, "zone": "zone.home", "lat": 52.2, "lon": 6.9},
        {"odo": 142.0, "zone": "zone.werk", "lat": 52.1, "lon": 6.1},
    ])
    tracker = TripTracker(FakeConfig(), ha, s)
    asyncio.run(tracker.handle_event(_event("0", "4")))
    asyncio.run(tracker.handle_event(_event("4", "0")))
    trip = s.list_trips()[0]
    assert trip.license_plate == "TEST-01"
    assert trip.start_city is not None  # geocode fake populated it
    assert trip.end_city is not None
    assert trip.source == "auto"


# ----------------------------------------------------------------- migration
def test_migration_adds_columns_to_old_schema():
    """A pre-1.1 trips table (no city/plate/source columns) upgrades in place."""
    import sqlite3
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        disk = sqlite3.connect(path)
        disk.execute(
            """CREATE TABLE trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT NOT NULL, end_time TEXT, km REAL,
                classification TEXT NOT NULL DEFAULT 'private',
                auto_classification TEXT NOT NULL DEFAULT 'private',
                overridden INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        disk.execute(
            "INSERT INTO trips (start_time, km, classification, created_at, updated_at) "
            "VALUES ('2026-01-01T08:00:00', 10, 'private', 'x', 'x')"
        )
        disk.commit()
        disk.close()

        # Opening with Storage must migrate and preserve the row.
        s = Storage(path)
        cols = {r["name"] for r in s._conn.execute("PRAGMA table_info(trips)").fetchall()}
        assert {"start_city", "end_city", "license_plate", "source"} <= cols
        trips = s.list_trips(year=2026)
        assert len(trips) == 1
        assert trips[0].km == 10
        assert trips[0].source == "auto"  # default backfilled
        s.close()
    finally:
        os.remove(path)


# -------------------------------------------------------- manual add/delete
def test_manual_add_and_delete():
    s = Storage(":memory:")
    tid = s.create_trip(Trip(
        start_time="2026-03-01T00:00:00", km=25, start_city="Enschede",
        end_city="Hengelo", license_plate="AB-12-C", source="manual",
        classification=PRIVATE, created_at="x", updated_at="x",
    ))
    trip = s.get_trip(tid)
    assert trip.source == "manual"
    assert trip.start_city == "Enschede"
    assert s.private_km_total(2026) == 25
    assert s.delete_trip(tid) is True
    assert s.get_trip(tid) is None
    assert s.private_km_total(2026) == 0


# -------------------------------------------------------- per-plate totals
def test_km_by_plate_combined():
    s = Storage(":memory:")
    for plate, km in [("A", 100), ("A", 50), ("B", 200), ("B", 150)]:
        s.create_trip(Trip(
            start_time="2026-05-01T00:00:00", km=km, license_plate=plate,
            classification=PRIVATE, created_at="x", updated_at="x",
        ))
    # business, different plate — must not count toward private
    s.create_trip(Trip(
        start_time="2026-05-01T00:00:00", km=999, license_plate="A",
        classification=BUSINESS, created_at="x", updated_at="x",
    ))
    assert s.private_km_total(2026) == 500  # combined across all plates
    breakdown = {b["plate"]: b["km"] for b in s.km_by_plate(2026, PRIVATE)}
    assert breakdown == {"A": 150, "B": 350}


# ------------------------------------------------------------- importer
def _build_workbook(plate, year, rows):
    """Build an in-memory xlsx mimicking the real sheet layout."""
    import openpyxl
    from io import BytesIO
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Kilometer Registratie"
    ws.cell(1, 1, "Kenteken"); ws.cell(1, 2, plate)
    ws.cell(2, 1, "Periode"); ws.cell(2, 2, year)
    headers = ["Datum", "Rit nr.", "Beginstand KM-teller", "Eindstand KM-teller",
               "Van - Naar", "Gereden Route", "Bezoekadres", "Karakter Rit (Z/P)", "Opmerkingen"]
    for c, h in enumerate(headers, start=1):
        ws.cell(6, c, h)
    r = 7
    for row in rows:
        for c, v in enumerate(row, start=1):
            ws.cell(r, c, v)
        r += 1
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_importer_parses_rows():
    from datetime import datetime as dt
    from app.importer import parse_workbook
    data = _build_workbook("Z-907-BF", 2026, [
        [dt(2026, 4, 24), 1, 68223, 68304, "Zwolle.- Enschede", None, None, "Z", "Ophalen"],
        [dt(2026, 4, 24), 2, 68304, 68308, "Enschede - Enschede ", None, None, "P", None],
        [dt(2026, 4, 25), 1, 68308, None, "Enschede - Zwolle", None, None, "Z", "Inleveren"],  # no eind -> skip
    ])
    result = parse_workbook(data)
    assert result.plate == "Z-907-BF"
    assert result.year == 2026
    assert len(result.trips) == 2
    assert result.skipped == 1
    t0 = result.trips[0]
    assert t0.km == 81
    assert t0.start_city == "Zwolle"  # stray dot normalized
    assert t0.end_city == "Enschede"
    assert t0.classification == BUSINESS
    t1 = result.trips[1]
    assert t1.classification == PRIVATE
    assert t1.start_city == "Enschede" and t1.end_city == "Enschede"


def test_importer_dedup_on_store():
    from datetime import datetime as dt
    from app.importer import parse_workbook
    from app.main import _store_import
    s = Storage(":memory:")
    data = _build_workbook("AB-12-C", 2026, [
        [dt(2026, 1, 5), 1, 1000, 1030, "Enschede - Hengelo", None, None, "Z", None],
    ])
    result = parse_workbook(data)
    inserted, dupes = _store_import(s, result.trips)
    assert inserted == 1 and dupes == 0
    # Re-import the same file: should dedup.
    inserted2, dupes2 = _store_import(s, result.trips)
    assert inserted2 == 0 and dupes2 == 1
    assert len(s.list_trips(year=2026)) == 1
