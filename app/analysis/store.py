"""Durable run history, request idempotency and a single paid job at a time."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS lot_analysis_runs (
 id TEXT PRIMARY KEY, lot_id INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
 input_hash TEXT NOT NULL, snapshot_hash TEXT NOT NULL, status TEXT NOT NULL,
 stage TEXT NOT NULL, input_json TEXT NOT NULL, report_json TEXT,
 error_code TEXT, message TEXT, created_at TEXT NOT NULL, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_analysis_lot ON lot_analysis_runs(lot_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_analysis_one_active ON lot_analysis_runs((1))
 WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS lot_analysis_requests (
 lot_id INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE, request_id TEXT NOT NULL,
 input_hash TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES lot_analysis_runs(id) ON DELETE CASCADE,
 PRIMARY KEY(lot_id, request_id)
);
"""


class RunConflict(Exception):
    def __init__(self, code, message):
        self.code, self.public_message = code, message
        super().__init__(message)


@contextmanager
def connection():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_store(recover=False):
    with connection() as conn:
        conn.executescript(SCHEMA)
        if recover:
            conn.execute("UPDATE lot_analysis_runs SET status='error',stage='interrupted',error_code='interrupted',"
                         "message='Сервис перезапущен. Анализ можно запустить снова.',finished_at=? "
                         "WHERE status IN ('queued','running')", (datetime.now(timezone.utc).isoformat(),))


def public_run(row):
    if not row:
        return None
    data = dict(row)
    data.pop("input_json", None)
    report = data.pop("report_json", None)
    data["report"] = json.loads(report) if report else None
    return data


def get_run(lot_id, run_id):
    with connection() as conn:
        return public_run(conn.execute("SELECT * FROM lot_analysis_runs WHERE lot_id=? AND id=?", (lot_id, run_id)).fetchone())


def history(lot_id):
    with connection() as conn:
        latest = conn.execute("SELECT * FROM lot_analysis_runs WHERE lot_id=? ORDER BY created_at DESC LIMIT 1", (lot_id,)).fetchone()
        success = conn.execute("SELECT * FROM lot_analysis_runs WHERE lot_id=? AND status='done' ORDER BY created_at DESC LIMIT 1", (lot_id,)).fetchone()
        recent = conn.execute("SELECT id,status,stage,message,created_at,finished_at FROM lot_analysis_runs WHERE lot_id=? ORDER BY created_at DESC LIMIT 10", (lot_id,)).fetchall()
        return {"latest": public_run(latest), "last_success": public_run(success), "history": [dict(x) for x in recent]}


def submit(lot_id, request_id, input_hash, snapshot_hash, payload, settings, now=None):
    now = now or datetime.now(timezone.utc)
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT * FROM lot_analysis_requests WHERE lot_id=? AND request_id=?", (lot_id, request_id)).fetchone()
        if previous:
            if previous["input_hash"] != input_hash:
                raise RunConflict("idempotency", "Этот идентификатор запроса уже использован с другими данными")
            row = conn.execute("SELECT * FROM lot_analysis_runs WHERE id=?", (previous["run_id"],)).fetchone()
            return public_run(row), False, "idempotent"
        active = conn.execute("SELECT * FROM lot_analysis_runs WHERE status IN ('queued','running')").fetchone()
        if active and (active["lot_id"] != lot_id or active["input_hash"] != input_hash):
            raise RunConflict("busy", "Уже выполняется анализ. Дождитесь его завершения")
        cached = active or conn.execute("SELECT * FROM lot_analysis_runs WHERE lot_id=? AND input_hash=? AND status='done' AND finished_at>=? ORDER BY finished_at DESC LIMIT 1",
                                      (lot_id, input_hash, (now - timedelta(minutes=settings.cache_minutes)).isoformat())).fetchone()
        if cached:
            conn.execute("INSERT INTO lot_analysis_requests VALUES (?,?,?,?)", (lot_id, request_id, input_hash, cached["id"]))
            return public_run(cached), False, "active" if active else "cache"
        day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        count = conn.execute("SELECT count(*) FROM lot_analysis_runs WHERE created_at>=?", (day,)).fetchone()[0]
        if count >= settings.daily_limit:
            raise RunConflict("daily_limit", "Дневной лимит анализов исчерпан (UTC)")
        run_id = uuid.uuid4().hex
        conn.execute("INSERT INTO lot_analysis_runs (id,lot_id,input_hash,snapshot_hash,status,stage,input_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (run_id, lot_id, input_hash, snapshot_hash, "queued", "queued", json.dumps(payload, ensure_ascii=False), now.isoformat()))
        conn.execute("INSERT INTO lot_analysis_requests VALUES (?,?,?,?)", (lot_id, request_id, input_hash, run_id))
        row = conn.execute("SELECT * FROM lot_analysis_runs WHERE id=?", (run_id,)).fetchone()
        return public_run(row), True, "new"


def progress(run_id, stage):
    with connection() as conn:
        conn.execute("UPDATE lot_analysis_runs SET status='running',stage=? WHERE id=? AND status IN ('queued','running')", (stage, run_id))


def finish(run_id, report=None, error=None):
    with connection() as conn:
        conn.execute("UPDATE lot_analysis_runs SET status=?,stage=?,report_json=?,error_code=?,message=?,finished_at=? WHERE id=? AND status IN ('queued','running')",
                     ("error" if error else "done", "error" if error else "done",
                      json.dumps(report, ensure_ascii=False, allow_nan=False) if report else None,
                      error.code if error else None, error.public_message if error else None,
                      datetime.now(timezone.utc).isoformat(), run_id))
