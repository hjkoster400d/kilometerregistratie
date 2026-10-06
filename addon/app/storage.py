"""SQLite storage for trips.

A trip is one ignition-on → ignition-off cycle (``source='auto'``), a bulk-imported
historical row (``source='import'``), or a hand-entered row (``source='manual'``).

Classification is stored twice: ``auto_classification`` (what the rule decided) and
``classification`` (the effective value, which a manual override can change).
``overridden`` records whether a human changed it, so the auto-classifier never
clobbers a manual decision.

Schema changes are applied idempotently in :meth:`Storage._migrate` so an existing
database on the add-on's ``/share`` map upgrades in place without data loss.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

BUSINESS = "business"
PRIVATE = "private"

SOURCE_AUTO = "auto"
SOURCE_IMPORT = "import"
SOURCE_MANUAL = "manual"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time           TEXT NOT NULL,
    end_time             TEXT,
    start_odo            REAL,
    end_odo              REAL,
    km                   REAL,
    start_lat            REAL,
    start_lon            REAL,
    end_lat              REAL,
    end_lon              REAL,
    start_zone           TEXT,
    end_zone             TEXT,
    start_city           TEXT,
    end_city             TEXT,
    license_plate        TEXT,
    source               TEXT NOT NULL DEFAULT 'auto',
    classification       TEXT NOT NULL DEFAULT 'private',
    auto_classification  TEXT NOT NULL DEFAULT 'private',
    overridden           INTEGER NOT NULL DEFAULT 0,
    note                 TEXT,
    raw_ignition_start   TEXT,
    raw_ignition_end     TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trips_start_time ON trips(start_time);
CREATE INDEX IF NOT EXISTS idx_trips_classification ON trips(classification);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Indexes that reference columns added by migration — created after _migrate().
_POST_MIGRATE_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_trips_plate ON trips(license_plate);
"""

# Columns added after the original 1.0 release. Applied idempotently.
_ADDED_COLUMNS = {
    "start_city": "TEXT",
    "end_city": "TEXT",
    "license_plate": "TEXT",
    "source": "TEXT NOT NULL DEFAULT 'auto'",
}


@dataclass
class Trip:
    id: Optional[int] = None
    start_time: str = ""
    end_time: Optional[str] = None
    start_odo: Optional[float] = None
    end_odo: Optional[float] = None
    km: Optional[float] = None
    start_lat: Optional[float] = None
    start_lon: Optional[float] = None
    end_lat: Optional[float] = None
    end_lon: Optional[float] = None
    start_zone: Optional[str] = None
    end_zone: Optional[str] = None
    start_city: Optional[str] = None
    end_city: Optional[str] = None
    license_plate: Optional[str] = None
    source: str = SOURCE_AUTO
    classification: str = PRIVATE
    auto_classification: str = PRIVATE
    overridden: int = 0
    note: Optional[str] = None
    raw_ignition_start: Optional[str] = None
    raw_ignition_end: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.executescript(_POST_MIGRATE_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -------------------------------------------------------------- migration
    def _migrate(self) -> None:
        """Add columns introduced after 1.0 to a pre-existing trips table."""
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(trips)").fetchall()
        }
        for column, decl in _ADDED_COLUMNS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE trips ADD COLUMN {column} {decl}")

    # ------------------------------------------------------------------ writes
    def create_trip(self, trip: Trip) -> int:
        data = asdict(trip)
        data.pop("id")
        cols = ", ".join(data.keys())
        placeholders = ", ".join(f":{k}" for k in data)
        cur = self._conn.execute(
            f"INSERT INTO trips ({cols}) VALUES ({placeholders})", data
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def update_trip(self, trip_id: int, **fields) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k} = :{k}" for k in fields)
        fields["id"] = trip_id
        self._conn.execute(
            f"UPDATE trips SET {assignments} WHERE id = :id", fields
        )
        self._conn.commit()

    def delete_trip(self, trip_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def import_exists(
        self,
        date: str,
        km: float,
        plate: Optional[str],
        start_city: Optional[str] = None,
        end_city: Optional[str] = None,
        note: Optional[str] = None,
    ) -> bool:
        """Dedup guard: has this exact imported row already been stored?"""
        row = self._conn.execute(
            "SELECT 1 FROM trips WHERE source = ? AND substr(start_time, 1, 10) = ? "
            "AND km = ? AND IFNULL(license_plate, '') = IFNULL(?, '') "
            "AND IFNULL(start_city, '') = IFNULL(?, '') "
            "AND IFNULL(end_city, '') = IFNULL(?, '') "
            "AND IFNULL(note, '') = IFNULL(?, '') LIMIT 1",
            (SOURCE_IMPORT, date, km, plate, start_city, end_city, note),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------- reads
    def get_trip(self, trip_id: int) -> Optional[Trip]:
        row = self._conn.execute(
            "SELECT * FROM trips WHERE id = ?", (trip_id,)
        ).fetchone()
        return Trip(**row) if row else None

    def list_trips(
        self,
        year: Optional[int] = None,
        classification: Optional[str] = None,
        plate: Optional[str] = None,
    ) -> list[Trip]:
        clauses, params = [], []
        if year is not None:
            clauses.append("substr(start_time, 1, 4) = ?")
            params.append(str(year))
        if classification is not None:
            clauses.append("classification = ?")
            params.append(classification)
        if plate is not None:
            clauses.append("license_plate = ?")
            params.append(plate)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM trips {where} ORDER BY start_time DESC", params
        ).fetchall()
        return [Trip(**row) for row in rows]

    def open_trip(self) -> Optional[Trip]:
        """The most recent auto-trip that has started but not yet ended."""
        row = self._conn.execute(
            "SELECT * FROM trips WHERE end_time IS NULL AND source = ? "
            "ORDER BY start_time DESC LIMIT 1",
            (SOURCE_AUTO,),
        ).fetchone()
        return Trip(**row) if row else None

    def plates(self, year: Optional[int] = None) -> list[str]:
        if year is not None:
            rows = self._conn.execute(
                "SELECT DISTINCT license_plate FROM trips "
                "WHERE substr(start_time, 1, 4) = ? AND license_plate IS NOT NULL",
                (str(year),),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT DISTINCT license_plate FROM trips WHERE license_plate IS NOT NULL"
            ).fetchall()
        return sorted(r["license_plate"] for r in rows if r["license_plate"])

    def _km_total(self, year: int, classification: str) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(km), 0) AS total FROM trips "
            "WHERE classification = ? AND substr(start_time, 1, 4) = ? "
            "AND km IS NOT NULL",
            (classification, str(year)),
        ).fetchone()
        return float(row["total"])

    def private_km_total(self, year: int) -> float:
        return self._km_total(year, PRIVATE)

    def business_km_total(self, year: int) -> float:
        return self._km_total(year, BUSINESS)

    def km_by_plate(self, year: int, classification: str) -> list[dict]:
        """Per-plate km breakdown for a classification in a given year."""
        rows = self._conn.execute(
            "SELECT COALESCE(license_plate, '—') AS plate, "
            "COALESCE(SUM(km), 0) AS total, COUNT(*) AS trips FROM trips "
            "WHERE classification = ? AND substr(start_time, 1, 4) = ? "
            "AND km IS NOT NULL GROUP BY license_plate ORDER BY total DESC",
            (classification, str(year)),
        ).fetchall()
        return [
            {"plate": r["plate"], "km": round(float(r["total"]), 1), "trips": r["trips"]}
            for r in rows
        ]

    # ---------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()
