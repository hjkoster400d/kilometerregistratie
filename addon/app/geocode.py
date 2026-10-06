"""Reverse-geocode lat/lon → city name via OpenStreetMap Nominatim.

Used for live auto-captured trips only (imported trips already carry city names).
Nominatim usage policy: descriptive User-Agent, max 1 request/sec, cache aggressively.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_USER_AGENT = "Kilometerregistratie HA add-on (github.com/hjkoster400d/kilometerregistratie)"

_CACHE_DECIMALS = 4  # ~11 m at Dutch latitudes
_city_cache: dict[tuple[float, float], Optional[str]] = {}

# Honour Nominatim's 1 req/s policy with an async lock + sleep after each call.
_rate_lock = asyncio.Lock()


def _round_coords(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, _CACHE_DECIMALS), round(lon, _CACHE_DECIMALS)


async def city_for(lat: Optional[float], lon: Optional[float]) -> Optional[str]:
    """Return the city/town/village for the given coordinates, or None on failure."""
    if lat is None or lon is None:
        return None

    key = _round_coords(lat, lon)
    if key in _city_cache:
        return _city_cache[key]

    async with _rate_lock:
        # Re-check inside the lock in case a concurrent call populated it.
        if key in _city_cache:
            return _city_cache[key]
        result = await _fetch(key[0], key[1])
        await asyncio.sleep(1.0)  # rate limit

    _city_cache[key] = result
    return result


async def _fetch(lat: float, lon: float) -> Optional[str]:
    """Single reverse-geocode call to Nominatim."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _NOMINATIM_URL,
                params={
                    "lat": lat,
                    "lon": lon,
                    "format": "jsonv2",
                    "zoom": 10,  # city level
                    "accept-language": "nl",
                },
                headers={"User-Agent": _USER_AGENT},
            )
            if resp.status_code != 200:
                log.warning("Nominatim HTTP %s for (%s, %s)", resp.status_code, lat, lon)
                return None
            data = resp.json()
            addr = data.get("address", {})
            return (
                addr.get("city")
                or addr.get("town")
                or addr.get("village")
                or addr.get("municipality")
                or data.get("name")
                or None
            )
    except Exception:
        log.exception("Nominatim lookup failed for (%s, %s)", lat, lon)
        return None
