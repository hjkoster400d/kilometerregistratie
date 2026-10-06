# Kilometerregistratie — werk/privé rittenregistratie

Service that tracks the mileage of lease cars by listening to Home Assistant
ignition-state events, and classifies each trip as **business** (zakelijk) or
**private** (privé). Keeps a running total of private kilometers against a yearly
budget (default 500 km/year, combined across all cars) and offers a web UI to
review, correct, import, and manually add trips.

## Features

- **Auto-capture**: subscribes to HA ignition events via WebSocket, snapshots
  odometer + GPS at trip start/end, computes km, classifies.
- **City names**: reverse-geocodes lat/lon via OSM Nominatim for auto-captured
  trips; imported/manual trips carry cities directly.
- **Excel import**: upload existing ritregistratie workbooks (.xlsx) to bulk-import
  historical trips from previous cars. Dedup guards prevent double-imports.
- **Manual add/remove**: add past trips by hand, delete any trip from the UI.
- **License plate per trip**: each trip records which car it belongs to. The plate
  comes from the import file or the current config setting for auto/manual trips.
- **Per-plate breakdown**: the private-km gauge shows a combined total across all
  cars, with a per-plate breakdown underneath.
- **Classification override**: one-click toggle between Zakelijk / Privé; manual
  override always wins over the auto-classifier.
- **JSON API**: `GET /api/trips`, `GET /api/summary`, `PATCH /api/trips/{id}`,
  `DELETE /api/trips/{id}`, `POST /api/trips`, `POST /api/import`.

## How it works

1. The car is integrated into Home Assistant via the
   [mbapi2020](https://github.com/ReneNulschDE/mbapi2020) custom component.
2. This service subscribes to HA's WebSocket event stream and watches the
   configured ignition entity.
   - `0 → non-zero` = **trip start** → snapshot odometer + location + zone + city.
   - `non-zero → 0` = **trip end** → snapshot again, compute km, classify, store.
3. **Classification rule**: a trip is `business` if the **start zone is work OR
   the end zone is work**. Everything else is `private`. Manual override always wins.
4. The web UI lists trips, shows a private-km-vs-budget gauge (with per-plate
   breakdown), and lets you flip the classification, edit notes, add/delete trips,
   and import Excel files.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `IGNITION_ENTITY` | `sensor.kzb_38_l_ignition_state` | trip trigger |
| `ODOMETER_ENTITY` | `sensor.kzb_38_l_odometer` | mileage source |
| `TRACKER_ENTITY` | `device_tracker.kzb_38_l_device_tracker` | location source |
| `WORK_ZONES` | `zone.werk` | comma-separated zones treated as "work" |
| `PRIVATE_KM_BUDGET` | `500` | yearly private-km limit |
| `LICENSE_PLATE` | `KZB-38-L` | current car's plate (for auto/manual trips) |
| `MIN_TRIP_KM` | `0.5` | ignore odometer deltas below this (noise) |
| `DB_PATH` | `./data/kilometers.db` | SQLite database path |

When running as a HA add-on, these are set via the add-on options in HA.

## Hosting

### As a Home Assistant add-on (recommended)

#### Install via Git repository

1. Push this repo to GitHub.
2. In HA: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, paste the
   repo URL, **Add**.
3. "Kilometerregistratie" appears → **Install** → **Start** → open from sidebar.

Updates: bump `version:` in `addon/config.yaml`, push, then **Update** in HA.

#### Install via local folder

Copy `addon/` to `/addons/kilometerregistratie/` on your HA host, then
**Store → ⋮ → Check for updates**.

### As a standalone container

```bash
cd addon && docker compose up -d --build
```

### Local dev

```bash
cd addon
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
pytest
```
