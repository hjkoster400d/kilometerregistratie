"""Home Assistant client.

Two responsibilities:
  * `get_state` / `get_zone_for_tracker` — REST reads used to snapshot the car's
    odometer and location at each trip boundary.
  * `listen_ignition` — a resilient WebSocket subscription that yields the raw
    ignition-state values as they change, so the trip tracker can detect
    start (0 -> non-zero) and end (non-zero -> 0) transitions.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

import httpx
import websockets

from .config import Config

log = logging.getLogger(__name__)


class HAClient:
    def __init__(self, config: Config):
        self.config = config
        self._headers = {"Authorization": f"Bearer {config.ha_token}"}

    # ----------------------------------------------------------------- REST
    async def get_state(self, entity_id: str) -> Optional[dict]:
        url = f"{self.config.ha_url}/api/states/{entity_id}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=self._headers)
            if resp.status_code == 200:
                return resp.json()
            log.warning("get_state %s -> HTTP %s", entity_id, resp.status_code)
            return None

    async def get_odometer(self) -> Optional[float]:
        state = await self.get_state(self.config.odometer_entity)
        if state is None:
            return None
        try:
            return float(state["state"])
        except (ValueError, KeyError, TypeError):
            return None

    async def get_location(self) -> dict:
        """Return {lat, lon, zone} for the tracker. zone is the first matching HA zone."""
        state = await self.get_state(self.config.tracker_entity)
        if state is None:
            return {"lat": None, "lon": None, "zone": None}
        attrs = state.get("attributes", {})
        zone = self._zone_from_state(state)
        return {
            "lat": attrs.get("latitude"),
            "lon": attrs.get("longitude"),
            "zone": zone,
        }

    @staticmethod
    def _zone_from_state(state: dict) -> Optional[str]:
        """Derive the zone for a device_tracker.

        mbapi2020 exposes an `in_zones` attribute (list of zone entity_ids). We take
        the first. If the tracker state itself is a friendly zone name (e.g. 'home'),
        fall back to that mapped to a zone entity_id when possible.
        """
        attrs = state.get("attributes", {})
        in_zones = attrs.get("in_zones")
        if isinstance(in_zones, list) and in_zones:
            return in_zones[0]
        # Fallback: tracker state 'home'/'not_home' or a zone friendly name.
        raw = state.get("state")
        if raw in (None, "not_home", "unknown", "unavailable"):
            return None
        if raw == "home":
            return "zone.home"
        return None

    # ------------------------------------------------------------- WebSocket
    async def listen_ignition(self) -> AsyncIterator[dict]:
        """Yield state_changed events for the ignition entity.

        Reconnects with backoff on any disconnect. Each yielded item is the HA
        event `data` dict: {entity_id, old_state, new_state}.
        """
        backoff = 1
        while True:
            try:
                async for event in self._subscribe():
                    backoff = 1
                    yield event
            except Exception as exc:  # noqa: BLE001 - resilience loop
                log.warning("WS listener error: %s; reconnecting in %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _subscribe(self) -> AsyncIterator[dict]:
        async with websockets.connect(self.config.ws_url, max_size=None) as ws:
            # 1. auth handshake
            await ws.recv()  # auth_required
            await ws.send(json.dumps({"type": "auth", "access_token": self.config.ha_token}))
            auth_result = json.loads(await ws.recv())
            if auth_result.get("type") != "auth_ok":
                raise RuntimeError(f"HA auth failed: {auth_result}")

            # 2. subscribe to state_changed events
            await ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
            await ws.recv()  # subscription result

            log.info("Subscribed to HA state_changed events")

            # 3. stream, filtering to the ignition entity
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") != "event":
                    continue
                data = msg["event"]["data"]
                if data.get("entity_id") == self.config.ignition_entity:
                    yield data
