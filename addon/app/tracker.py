"""Trip tracker — the core state machine.

Consumes ignition `state_changed` events and:
  * on 0 -> non-zero: opens a trip, snapshotting odometer + location + zone.
  * on non-zero -> 0: closes the open trip, snapshots again, computes km, classifies.

Transition detection is a pure function (`detect_transition`) so it can be unit
tested without HA or a database. Odometer deltas below `MIN_TRIP_KM` are discarded
as noise (e.g. GPS jitter or a key-on with no real movement).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .classify import classify
from .config import Config
from .ha_client import HAClient
from .storage import Storage, Trip

log = logging.getLogger(__name__)

TRIP_START = "start"
TRIP_END = "end"


def _is_off(value: Optional[str]) -> bool:
    """True when the ignition value represents 'off'.

    Treat 0, empty, 'off', and HA's unknown/unavailable as off. Any other numeric
    value counts as 'on' — so we do not depend on knowing whether running is 1/2/4.
    """
    if value in (None, "", "0", "off", "unknown", "unavailable"):
        return True
    return False


def detect_transition(old: Optional[str], new: Optional[str]) -> Optional[str]:
    """Return TRIP_START, TRIP_END, or None for an ignition value change."""
    old_off, new_off = _is_off(old), _is_off(new)
    if old_off and not new_off:
        return TRIP_START
    if not old_off and new_off:
        return TRIP_END
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TripTracker:
    def __init__(self, config: Config, ha: HAClient, storage: Storage):
        self.config = config
        self.ha = ha
        self.storage = storage

    async def handle_event(self, data: dict) -> None:
        old = (data.get("old_state") or {}).get("state")
        new = (data.get("new_state") or {}).get("state")
        transition = detect_transition(old, new)
        if transition == TRIP_START:
            await self._start_trip(raw_value=new)
        elif transition == TRIP_END:
            await self._end_trip(raw_value=new, old_value=old)

    async def _start_trip(self, raw_value: Optional[str]) -> None:
        existing = self.storage.open_trip()
        if existing is not None:
            log.warning("Ignition on while trip %s still open; ignoring", existing.id)
            return
        odo = await self.ha.get_odometer()
        loc = await self.ha.get_location()
        trip = Trip(
            start_time=_now_iso(),
            start_odo=odo,
            start_lat=loc["lat"],
            start_lon=loc["lon"],
            start_zone=loc["zone"],
            raw_ignition_start=raw_value,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        trip_id = self.storage.create_trip(trip)
        log.info("Trip %s started: odo=%s zone=%s", trip_id, odo, loc["zone"])

    async def _end_trip(self, raw_value: Optional[str], old_value: Optional[str]) -> None:
        trip = self.storage.open_trip()
        if trip is None:
            log.warning("Ignition off but no open trip; ignoring")
            return
        odo = await self.ha.get_odometer()
        loc = await self.ha.get_location()
        km = None
        if odo is not None and trip.start_odo is not None:
            km = round(odo - trip.start_odo, 1)

        auto = classify(trip.start_zone, loc["zone"], self.config.work_zones)

        # Discard noise: tiny or non-positive odometer deltas are not real trips.
        if km is not None and km < self.config.min_trip_km:
            log.info("Trip %s discarded as noise (km=%s)", trip.id, km)
            self.storage.update_trip(
                            trip.id,
                            end_time=_now_iso(),
                            end_odo=odo,
                            km=None,
                            note=f"auto-discarded: {km} km below MIN_TRIP_KM",
                            updated_at=_now_iso(),
                        )
            return

        self.storage.update_trip(
            trip.id,
            end_time=_now_iso(),
            end_odo=odo,
            km=km,
            end_lat=loc["lat"],
            end_lon=loc["lon"],
            end_zone=loc["zone"],
            classification=auto,
            auto_classification=auto,
            raw_ignition_end=old_value,
            updated_at=_now_iso(),
        )
        log.info(
            "Trip %s ended: km=%s %s->%s class=%s",
            trip.id, km, trip.start_zone, loc["zone"], auto,
        )

    async def run(self) -> None:
        """Main loop: stream ignition events forever."""
        log.info("TripTracker running; watching %s", self.config.ignition_entity)
        async for event in self.ha.listen_ignition():
            try:
                await self.handle_event(event)
            except Exception:  # noqa: BLE001 - never let one event kill the loop
                log.exception("Error handling ignition event")
