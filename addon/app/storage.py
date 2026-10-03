"""SQLite storage for trips.

A trip is one ignition-on → ignition-off cycle. Classification is stored twice:
`auto_classification` (what the rule decided) and `classification` (the effective
value, which a manual override can change). `overridden` records whether a human
changed it, so the auto-classifier never clobbers a manual decision.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

BUSINESS = "business"
PRIVATE = "private"

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
"""


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
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

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

    # ------------------------------------------------------------------- reads
    def get_trip(self, trip_id: int) -> Optional[Trip]:
        row = self._conn.execute(
            "SELECT * FROM trips WHERE id = ?", (trip_id,)
        ).fetchone()
        return Trip(**row) if row else None

    def list_trips(
        self, year: Optional[int] = None, classification: Optional[str] = None
    ) -> list[Trip]:
        clauses, params = [], []
        if year is not None:
            clauses.append("substr(start_time, 1, 4) = ?")
            params.append(str(year))
        if classification is not None:
            clauses.append("classification = ?")
            params.append(classification)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM trips {where} ORDER BY start_time DESC", params
        ).fetchall()
        return [Trip(**row) for row in rows]

    def open_trip(self) -> Optional[Trip]:
        """The most recent trip that has started but not yet ended."""
        row = self._conn.execute(
            "SELECT * FROM trips WHERE end_time IS NULL ORDER BY start_time DESC LIMIT 1"
        ).fetchone()
        return Trip(**row) if row else None

    def private_km_total(self, year: int) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(km), 0) AS total FROM trips "
            "WHERE classification = ? AND substr(start_time, 1, 4) = ? "
            "AND km IS NOT NULL",
            (PRIVATE, str(year)),
        ).fetchone()
        return float(row["total"])

    def business_km_total(self, year: int) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(km), 0) AS total FROM trips "
            "WHERE classification = ? AND substr(start_time, 1, 4) = ? "
            "AND km IS NOT NULL",
            (BUSINESS, str(year)),
        ).fetchone()
        return float(row["total"])
