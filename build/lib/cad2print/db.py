"""SQLite persistence for jobs (stdlib sqlite3, one table)."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .presets import Overrides

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    created       TEXT NOT NULL,
    updated       TEXT NOT NULL,
    status        TEXT NOT NULL,   -- queued | running | done | failed
    stage         TEXT NOT NULL,   -- queued | exporting | slicing | handing_off | done
    stages_seen   TEXT NOT NULL DEFAULT '[]',
    preset        TEXT NOT NULL,
    overrides     TEXT NOT NULL,
    parts         TEXT NOT NULL,
    handoff_name  TEXT,
    error         TEXT,
    log_tail      TEXT,
    duration_s    REAL
);
"""


@dataclass(frozen=True)
class PartRef:
    did: str
    wvm: str
    wvmid: str
    eid: str
    part_id: str
    name: str
    qty: int = 1


@dataclass(frozen=True)
class JobSpec:
    parts: list[PartRef]
    preset: str
    overrides: Overrides


@dataclass
class Job:
    id: str
    created: str
    updated: str
    status: str
    stage: str
    spec: JobSpec
    stages_seen: list[str] = field(default_factory=list)
    handoff_name: str | None = None
    error: str | None = None
    log_tail: str | None = None
    duration_s: float | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {"done", "failed"}

    @property
    def title(self) -> str:
        names = [p.name for p in self.spec.parts]
        if not names:
            return "(no parts)"
        return names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def create(self, spec: JobSpec) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], created=_now(), updated=_now(),
                  status="queued", stage="queued", spec=spec)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO jobs (id, created, updated, status, stage, stages_seen, preset, overrides, parts)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (job.id, job.created, job.updated, job.status, job.stage, "[]", spec.preset,
                 json.dumps(asdict(spec.overrides)), json.dumps([asdict(p) for p in spec.parts])),
            )
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def list_recent(self, limit: int = 50) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def set_stage(self, job_id: str, stage: str, status: str = "running") -> None:
        with self._lock, self._conn:
            seen = json.loads(self._conn.execute(
                "SELECT stages_seen FROM jobs WHERE id = ?", (job_id,)).fetchone()[0])
            seen.append(stage)
            self._conn.execute(
                "UPDATE jobs SET stage = ?, status = ?, stages_seen = ?, updated = ? WHERE id = ?",
                (stage, status, json.dumps(seen), _now(), job_id),
            )

    def finish(self, job_id: str, *, handoff_name: str | None = None, error: str | None = None,
               log_tail: str | None = None, duration_s: float | None = None) -> None:
        status = "failed" if error else "done"
        with self._lock, self._conn:
            if status == "done":
                seen = json.loads(self._conn.execute(
                    "SELECT stages_seen FROM jobs WHERE id = ?", (job_id,)).fetchone()[0])
                seen.append("done")
                self._conn.execute("UPDATE jobs SET stage = 'done', stages_seen = ? WHERE id = ?",
                                   (json.dumps(seen), job_id))
            self._conn.execute(
                "UPDATE jobs SET status = ?, handoff_name = ?, error = ?, log_tail = ?, duration_s = ?,"
                " updated = ? WHERE id = ?",
                (status, handoff_name, error, log_tail, duration_s, _now(), job_id),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_job(row: sqlite3.Row) -> Job:
    spec = JobSpec(
        parts=[PartRef(**p) for p in json.loads(row["parts"])],
        preset=row["preset"],
        overrides=Overrides(**json.loads(row["overrides"])),
    )
    return Job(
        id=row["id"], created=row["created"], updated=row["updated"], status=row["status"],
        stage=row["stage"], spec=spec, stages_seen=json.loads(row["stages_seen"]),
        handoff_name=row["handoff_name"], error=row["error"], log_tail=row["log_tail"],
        duration_s=row["duration_s"],
    )
