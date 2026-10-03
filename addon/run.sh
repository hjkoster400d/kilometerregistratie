#!/usr/bin/with-contenv bashio
# Translate Home Assistant add-on options into environment variables, then run the app.
set -e

export IGNITION_ENTITY="$(bashio::config 'ignition_entity')"
export ODOMETER_ENTITY="$(bashio::config 'odometer_entity')"
export TRACKER_ENTITY="$(bashio::config 'tracker_entity')"
export PRIVATE_KM_BUDGET="$(bashio::config 'private_km_budget')"
export MIN_TRIP_KM="$(bashio::config 'min_trip_km')"

# work_zones is a list in options; join into the comma-separated form the app expects.
WORK_ZONES=""
for zone in $(bashio::config 'work_zones'); do
  WORK_ZONES="${WORK_ZONES:+$WORK_ZONES,}${zone}"
done
export WORK_ZONES

# Persist the SQLite DB on the add-on's /share map so it survives restarts/updates.
export DB_PATH="/share/kilometerregistratie/kilometers.db"
mkdir -p /share/kilometerregistratie

# HA API access via the Supervisor is automatic through SUPERVISOR_TOKEN.
bashio::log.info "Starting Kilometerregistratie (work_zones=${WORK_ZONES}, budget=${PRIVATE_KM_BUDGET})"

exec uvicorn app.main:app --host 0.0.0.0 --port 8080
