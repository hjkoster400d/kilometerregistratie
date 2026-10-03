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
