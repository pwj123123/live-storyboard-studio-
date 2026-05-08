"""SQLite 기반 작업 상태 추적 모듈.

웹(Streamlit), API(FastAPI), 향후 iOS Live Activity 모두 동일한 데이터 소스를 사용한다.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).parent / "data" / "jobs.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    type          TEXT NOT NULL,
    title         TEXT,
    status        TEXT NOT NULL,
    progress      REAL NOT NULL DEFAULT 0,
    current_step  TEXT,
    step_index    INTEGER NOT NULL DEFAULT 0,
    total_steps   INTEGER NOT NULL DEFAULT 0,
    result_json   TEXT,
    error         TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    completed_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


@contextmanager
def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)


def create_job(user_id: str, type: str, title: str = "", total_steps: int = 0) -> str:
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO jobs (id, user_id, type, title, status, progress, total_steps, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (job_id, user_id, type, title, STATUS_QUEUED, total_steps, now, now),
        )
    return job_id


def update_progress(
    job_id: str,
    *,
    progress: Optional[float] = None,
    current_step: Optional[str] = None,
    step_index: Optional[int] = None,
    status: Optional[str] = None,
) -> None:
    fields: list[str] = []
    values: list[Any] = []
    if progress is not None:
        fields.append("progress = ?")
        values.append(max(0.0, min(100.0, float(progress))))
    if current_step is not None:
        fields.append("current_step = ?")
        values.append(current_step)
    if step_index is not None:
        fields.append("step_index = ?")
        values.append(step_index)
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if not fields:
        return
    fields.append("updated_at = ?")
    values.append(time.time())
    values.append(job_id)
    with _conn() as c:
        c.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)


def complete_job(job_id: str, result: Optional[dict] = None) -> None:
    now = time.time()
    payload = json.dumps(result, ensure_ascii=False) if result is not None else None
    with _conn() as c:
        c.execute(
            """
            UPDATE jobs
               SET status = ?, progress = 100, result_json = ?, completed_at = ?, updated_at = ?
             WHERE id = ?
            """,
            (STATUS_COMPLETED, payload, now, now, job_id),
        )


def fail_job(job_id: str, error: str) -> None:
    now = time.time()
    with _conn() as c:
        c.execute(
            """
            UPDATE jobs
               SET status = ?, error = ?, completed_at = ?, updated_at = ?
             WHERE id = ?
            """,
            (STATUS_FAILED, error[:2000], now, now, job_id),
        )


def get_job(job_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_dict(row)


def list_jobs(user_id: Optional[str] = None, limit: int = 20) -> list[dict]:
    with _conn() as c:
        if user_id is None:
            rows = c.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [_row_to_dict(r) for r in rows if r]


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    raw = d.pop("result_json", None)
    if raw:
        try:
            d["result"] = json.loads(raw)
        except json.JSONDecodeError:
            d["result"] = None
    else:
        d["result"] = None
    return d


init_db()
