"""Application configuration.

Values come from environment variables (optionally via a .env file). The Home
Assistant URL and token fall back to ~/.kiro/settings/homeassistant.json when not
provided in the environment, matching the convention used by the homelab tooling.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_SETTINGS_FILE = Path.home() / ".kiro" / "settings" / "homeassistant.json"


def _ha_from_settings() -> tuple[str | None, str | None]:
    """Read url/token from the shared HA settings file, if present."""
    try:
        data = json.loads(_SETTINGS_FILE.read_text())
        return data.get("url"), data.get("token")
    except (OSError, json.JSONDecodeError):
        return None, None


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    ha_url: str
    ha_token: str
    ignition_entity: str
    odometer_entity: str
    tracker_entity: str
    work_zones: list[str]
    private_km_budget: float
    min_trip_km: float
    license_plate: str
    db_path: str

    @classmethod
    def load(cls) -> "Config":
        # Priority: explicit env > Supervisor (add-on) > shared settings file.
        settings_url, settings_token = _ha_from_settings()
        supervisor_token = os.getenv("SUPERVISOR_TOKEN")

        if os.getenv("HA_URL"):
            ha_url = os.getenv("HA_URL", "").rstrip("/")
            ha_token = os.getenv("HA_TOKEN", "")
        elif supervisor_token:
            # Running as a Home Assistant add-on: talk to the Supervisor proxy.
            ha_url = "http://supervisor/core"
            ha_token = supervisor_token
        else:
            ha_url = (settings_url or "").rstrip("/")
            ha_token = settings_token or ""

        if not ha_url or not ha_token:
            raise RuntimeError(
                "Home Assistant URL/token not configured. Set HA_URL and HA_TOKEN, "
                "run as a Supervisor add-on (SUPERVISOR_TOKEN), "
                f"or provide them in {_SETTINGS_FILE}."
            )
        return cls(
            ha_url=ha_url,
            ha_token=ha_token,
            ignition_entity=os.getenv("IGNITION_ENTITY", "sensor.kzb_38_l_ignition_state"),
            odometer_entity=os.getenv("ODOMETER_ENTITY", "sensor.kzb_38_l_odometer"),
            tracker_entity=os.getenv(
                "TRACKER_ENTITY", "device_tracker.kzb_38_l_device_tracker"
            ),
            work_zones=_split_csv(os.getenv("WORK_ZONES", "zone.werk")),
            private_km_budget=float(os.getenv("PRIVATE_KM_BUDGET", "500")),
            min_trip_km=float(os.getenv("MIN_TRIP_KM", "0.5")),
            license_plate=os.getenv("LICENSE_PLATE", "KZB-38-L"),
            db_path=os.getenv("DB_PATH", "./data/kilometers.db"),
        )

    @property
    def ws_url(self) -> str:
        base = self.ha_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base}/api/websocket"
