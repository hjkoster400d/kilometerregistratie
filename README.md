# Kilometerregistratie — werk/privé rittenregistratie

Standalone service that tracks the mileage of a Mercedes GLB 200 (EV) by listening to
Home Assistant ignition-state events, and classifies each trip as **business** (zakelijk)
or **private** (privé). Keeps a running total of private kilometers against a yearly
budget (default 500 km/year) and offers a web UI to review and correct trips.

## How it works

1. The car is integrated into Home Assistant via the
   [mbapi2020](https://github.com/ReneNulschDE/mbapi2020) custom component.
2. This service subscribes to HA's WebSocket event stream and watches
   `sensor.kzb_38_l_ignition_state`.
   - `0 → non-zero` = **trip start** → snapshot odometer + location + zone.
   - `non-zero → 0` = **trip end** → snapshot again, compute km, classify, store.
3. **Classification rule**: a trip is `business` if the **start zone is work OR the end
   zone is work**. Everything else is `private`. A manual override always wins.
4. A FastAPI web UI lists trips, shows a private-km-vs-budget gauge, and lets you flip
   the business/private flag or edit a trip.

## Entities (ground truth from the live HA instance)

| Signal | Entity | Notes |
|---|---|---|
| Odometer | `sensor.kzb_38_l_odometer` | km, `total_increasing` |
| Ignition | `sensor.kzb_38_l_ignition_state` | numeric; `0` = off, non-zero = on |
| Location | `device_tracker.kzb_38_l_device_tracker` | GPS, has `latitude`/`longitude`/`in_zones` |
| Work zone | `zone.werk` | tighten radius before relying on it |

> **Ignition mapping note**: at scaffold time the car had not been driven while the
> recorder was active, so only `0` (off) was observed. The listener treats *any* non-zero
> value as "on", so the exact running value (1/2/4) does not matter. Raw values seen on
> each trip are stored in `trips.raw_ignition_start` / `raw_ignition_end` for later
> verification.

## Configuration

Copy `.env.example` to `.env` and fill it in. The HA URL and token are read from
`~/.kiro/settings/homeassistant.json` by default if not set in the environment.

| Variable | Default | Meaning |
|---|---|---|
| `HA_URL` | from settings file | Home Assistant base URL |
| `HA_TOKEN` | from settings file | Long-lived access token |
| `IGNITION_ENTITY` | `sensor.kzb_38_l_ignition_state` | trip trigger |
| `ODOMETER_ENTITY` | `sensor.kzb_38_l_odometer` | mileage source |
| `TRACKER_ENTITY` | `device_tracker.kzb_38_l_device_tracker` | location source |
| `WORK_ZONES` | `zone.werk` | comma-separated zones treated as "work" |
| `PRIVATE_KM_BUDGET` | `500` | yearly private-km limit |
| `DB_PATH` | `./data/kilometers.db` | SQLite database path |
| `MIN_TRIP_KM` | `0.5` | ignore odometer deltas below this (noise) |

## Hosting

### As a Home Assistant add-on (recommended)

This install runs the Supervisor, so the app runs as an add-on. It talks to HA
internally via the Supervisor token — **no long-lived token needed** — and appears
in the HA sidebar via ingress.

#### Repository layout

```
.                       <- Git repo root (add-on repository)
├── repository.yaml     <- declares this as a HA add-on store
├── README.md
└── addon/              <- the add-on itself
    ├── config.yaml     <- manifest (ingress, options schema, /share map)
    ├── build.yaml      <- per-arch HA base images
    ├── Dockerfile      <- add-on image (HA base + app)
    ├── run.sh          <- reads options via bashio, launches uvicorn
    ├── app/            <- application code
    └── ...
```

#### Install via Git repository (easiest to publish)

1. Push this repo to GitHub (public or private — private needs HA to have access).
2. In HA: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, paste the repo
   URL (e.g. `https://github.com/rikkoster/kilometerregistratie`), **Add**.
3. The "Kilometerregistratie" add-on appears in the store. Open it → **Install**.
   HA builds the image **on your HA host** from `addon/Dockerfile` (first build
   takes a few minutes on a Pi).
4. Adjust options (work zones, budget) if needed, **Start**, open from the sidebar.

Updates: bump `version:` in `addon/config.yaml`, `git push`, then **Update** in HA.

#### Install via local folder (no Git)

Copy the `addon/` folder into your HA config share as `/addons/kilometerregistratie/`
(via the Samba/SSH add-on), then **Settings → Add-ons → Store → ⋮ → Check for
updates** — it appears as a local add-on.

The SQLite DB persists at `/share/kilometerregistratie/kilometers.db`.

#### Pre-built images from a registry (optional, faster installs)

By default HA builds on your host. To instead PULL a pre-built image (no on-device
build — handy for slow hardware):

1. `docker login -u <dockerhubuser>`
2. `DOCKERHUB_USER=<dockerhubuser> addon/scripts/build-push.sh` — builds and pushes
   one image per architecture (`kilometerregistratie-amd64`, `-aarch64`, `-armv7`)
   using the official HA builder (buildx fallback included).
3. In `addon/config.yaml`, uncomment and set
   `image: "<dockerhubuser>/kilometerregistratie-{arch}"` (HA substitutes `{arch}`).
4. Bump `version:` and install/update the add-on.

> Public registry repos let the Supervisor pull without a login. The image contains
> only app code — the Supervisor token is injected at runtime, so nothing secret is
> baked in.

### As a standalone Docker container

If you prefer to run it outside HA (points at HA over the network, needs a
long-lived token in `~/.kiro/settings/homeassistant.json` or `HA_URL`/`HA_TOKEN`):

```bash
cd addon
docker compose up -d --build   # uses Dockerfile.standalone
```

Then open http://localhost:8080.

### Local (dev)
```bash
cd addon
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit as needed
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Tests
```bash
cd addon
pip install -r requirements-dev.txt
pytest
```
