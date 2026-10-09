"""Storage for live reports and incidents.

:class:`BaseStore` is the interface the live service, the agent and the AWS
handlers use. :class:`Store` is the local SQLite implementation (stdlib only);
:class:`upstream.intake.dynamo.DynamoStore` implements the same interface on
DynamoDB.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from ..reports import Report

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS supply (
    cycle INTEGER PRIMARY KEY,
    start_min REAL NOT NULL,
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    household TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    supply_start REAL NOT NULL,
    t_obs REAL NOT NULL,
    received_at TEXT NOT NULL,
    turbidity REAL,
    chlorine REAL,
    answer TEXT,
    source TEXT NOT NULL,
    level TEXT,
    reasons TEXT NOT NULL DEFAULT '[]',
    flags TEXT NOT NULL DEFAULT '[]',
    reading TEXT,
    photo TEXT,
    sha256 TEXT,
    phash TEXT,
    taken_at TEXT
);
CREATE INDEX IF NOT EXISTS reports_cycle ON reports(cycle);
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wa_sessions (
    sender TEXT PRIMARY KEY,
    report_id TEXT NOT NULL
);
"""


INCIDENT_STATUSES = ("open", "awaiting_approval", "approved", "rejected", "notified", "closed")


def row_to_report(d: dict) -> Report:
    def js(v, default):
        if v is None:
            return default
        return json.loads(v) if isinstance(v, str) else v
    return Report(
        id=d["id"], household=d["household"], cycle=int(d["cycle"]), supply_start=float(d["supply_start"]),
        t_obs=float(d["t_obs"]), turbidity=None if d.get("turbidity") is None else float(d["turbidity"]),
        chlorine=None if d.get("chlorine") is None else float(d["chlorine"]), answer=d.get("answer"),
        source=d["source"], level=d.get("level"), reasons=js(d.get("reasons"), []), flags=js(d.get("flags"), []),
        reading=js(d.get("reading"), None))


class BaseStore:
    """Interface shared by the SQLite and DynamoDB stores."""

    def get_meta(self, key: str) -> str | None: raise NotImplementedError
    def set_meta(self, key: str, value: str) -> None: raise NotImplementedError
    def add_supply(self, start_min: float, started_at: str) -> int: raise NotImplementedError
    def current_supply(self) -> dict | None: raise NotImplementedError
    def next_report_id(self, cycle: int) -> str: raise NotImplementedError
    def add_report(self, r: Report, received_at: str, photo: str | None = None, sha256: str | None = None,
                   phash: str | None = None, taken_at: str | None = None) -> None: raise NotImplementedError
    def update_levels(self, reports: list[Report]) -> None: raise NotImplementedError
    def set_answer(self, report_id: str, answer: str) -> bool: raise NotImplementedError
    def rows(self, min_cycle: int | None = None) -> list[dict]: raise NotImplementedError
    def find_photo(self, sha256: str | None = None, taken_at: str | None = None) -> list[dict]:
        raise NotImplementedError
    def phashes(self) -> list[dict]: raise NotImplementedError
    def set_session(self, sender: str, report_id: str) -> None: raise NotImplementedError
    def session(self, sender: str) -> str | None: raise NotImplementedError
    def put_incident(self, incident: dict) -> None: raise NotImplementedError
    def get_incident(self, incident_id: str) -> dict | None: raise NotImplementedError
    def list_incidents(self) -> list[dict]: raise NotImplementedError
    def reset(self) -> None: raise NotImplementedError

    def reports(self, min_cycle: int | None = None) -> list[Report]:
        return [row_to_report(d) for d in self.rows(min_cycle)]

    def update_incident(self, incident_id: str, **changes) -> dict:
        inc = self.get_incident(incident_id)
        if inc is None:
            raise KeyError(incident_id)
        if "status" in changes and changes["status"] not in INCIDENT_STATUSES:
            raise ValueError(f"bad incident status {changes['status']!r}")
        inc.update(changes)
        self.put_incident(inc)
        return inc


class Store(BaseStore):
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    # ------------------------------------------------------------- meta
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
            self._db.commit()

    # ----------------------------------------------------------- supply
    def add_supply(self, start_min: float, started_at: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(MAX(cycle), -1) AS c FROM supply").fetchone()
            cycle = int(row["c"]) + 1
            self._db.execute("INSERT INTO supply(cycle, start_min, started_at) VALUES (?, ?, ?)",
                             (cycle, start_min, started_at))
            self._db.commit()
        return cycle

    def current_supply(self) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM supply ORDER BY cycle DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    # ---------------------------------------------------------- reports
    def next_report_id(self, cycle: int) -> str:
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) AS n FROM reports WHERE cycle = ?", (cycle,)).fetchone()["n"]
        return f"L{cycle:03d}-{int(n) + 1:03d}"

    def add_report(self, r: Report, received_at: str, photo: str | None = None, sha256: str | None = None,
                   phash: str | None = None, taken_at: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO reports(id, household, cycle, supply_start, t_obs, received_at, turbidity, chlorine,"
                " answer, source, level, reasons, flags, reading, photo, sha256, phash, taken_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r.id, r.household, r.cycle, r.supply_start, r.t_obs, received_at, r.turbidity, r.chlorine,
                 r.answer, r.source, r.level, json.dumps(r.reasons), json.dumps(r.flags),
                 json.dumps(r.reading) if r.reading is not None else None, photo, sha256, phash, taken_at))
            self._db.commit()

    def update_levels(self, reports: list[Report]) -> None:
        with self._lock:
            self._db.executemany("UPDATE reports SET level = ?, reasons = ? WHERE id = ?",
                                 [(r.level, json.dumps(r.reasons), r.id) for r in reports])
            self._db.commit()

    def set_answer(self, report_id: str, answer: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE reports SET answer = ? WHERE id = ?", (answer, report_id))
            self._db.commit()
        return cur.rowcount > 0

    def rows(self, min_cycle: int | None = None) -> list[dict]:
        q = "SELECT * FROM reports"
        args: tuple = ()
        if min_cycle is not None:
            q += " WHERE cycle >= ?"
            args = (min_cycle,)
        q += " ORDER BY t_obs, id"
        with self._lock:
            return [dict(r) for r in self._db.execute(q, args).fetchall()]

    def find_photo(self, sha256: str | None = None, taken_at: str | None = None) -> list[dict]:
        clauses, args = [], []
        if sha256:
            clauses.append("sha256 = ?")
            args.append(sha256)
        if taken_at:
            clauses.append("taken_at = ?")
            args.append(taken_at)
        if not clauses:
            return []
        with self._lock:
            return [dict(r) for r in self._db.execute(
                f"SELECT id, household, cycle, sha256, phash, taken_at FROM reports WHERE {' OR '.join(clauses)}",
                tuple(args)).fetchall()]

    def phashes(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT id, household, cycle, phash FROM reports WHERE phash IS NOT NULL").fetchall()]

    # -------------------------------------------------------- incidents
    def put_incident(self, incident: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO incidents(id, body) VALUES (?, ?)",
                             (incident["id"], json.dumps(incident)))
            self._db.commit()

    def get_incident(self, incident_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        return json.loads(row["body"]) if row else None

    def list_incidents(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT body FROM incidents").fetchall()
        return sorted((json.loads(r["body"]) for r in rows), key=lambda i: i.get("opened_at", ""), reverse=True)

    # -------------------------------------------------------- whatsapp
    def set_session(self, sender: str, report_id: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO wa_sessions(sender, report_id) VALUES (?, ?)",
                             (sender, report_id))
            self._db.commit()

    def session(self, sender: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT report_id FROM wa_sessions WHERE sender = ?", (sender,)).fetchone()
        return row["report_id"] if row else None

    def reset(self) -> None:
        with self._lock:
            self._db.executescript("DELETE FROM reports; DELETE FROM supply; DELETE FROM meta; DELETE FROM wa_sessions;"
                                   " DELETE FROM incidents;")
            self._db.commit()
