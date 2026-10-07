"""SQLite 저장소. 한 파일 DB, WAL 모드(웹 서버와 수집기가 동시에 접근)."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .config import KST

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    org TEXT NOT NULL,
    title TEXT NOT NULL,
    category_raw TEXT,
    supply_type TEXT,
    notice_kind TEXT,
    sido TEXT,
    sigungu TEXT,
    region_raw TEXT,
    complex_name TEXT,
    households INTEGER,
    area_min REAL,
    area_max REAL,
    deposit_min INTEGER,
    deposit_max INTEGER,
    rent_min INTEGER,
    rent_max INTEGER,
    price_min INTEGER,
    price_max INTEGER,
    posted_date TEXT,
    apply_start TEXT,
    apply_end TEXT,
    status TEXT,
    url TEXT,
    raw_summary TEXT,
    body_text TEXT,
    complex_text TEXT,
    is_correction INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    revised_at TEXT,
    cooperative_suspect INTEGER NOT NULL DEFAULT 0,
    cooperative_reason TEXT,
    coop_override INTEGER NOT NULL DEFAULT 0,
    detail_fetched INTEGER NOT NULL DEFAULT 0,
    detail_error TEXT,
    detail_attempts INTEGER NOT NULL DEFAULT 0,
    baseline INTEGER NOT NULL DEFAULT 0,
    dup_key TEXT,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_notices_first_seen ON notices(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_notices_dup_key ON notices(dup_key);

CREATE TABLE IF NOT EXISTS filters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    config TEXT NOT NULL,
    webhook_url TEXT,
    enabled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id INTEGER NOT NULL REFERENCES notices(id) ON DELETE CASCADE,
    filter_id INTEGER NOT NULL REFERENCES filters(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'new',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    error TEXT,
    UNIQUE(notice_id, filter_id, revision, kind)
);
CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS source_status (
    source TEXT PRIMARY KEY,
    last_run_at TEXT,
    last_success_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_count INTEGER,
    last_new INTEGER,
    next_attempt_at TEXT,
    alerted INTEGER NOT NULL DEFAULT 0,
    baseline_done INTEGER NOT NULL DEFAULT 0,
    state TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trigger TEXT,
    summary TEXT
);
"""


def now_iso() -> str:
    return datetime.now(KST).replace(microsecond=0).isoformat()


def connect(db_path: Path | str) -> sqlite3.Connection:
    db_path = Path(db_path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(db_path.parent, 0o700)
        except OSError:
            pass
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    if str(db_path) != ":memory:":
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass
    return conn


# ---------------------------------------------------------------- settings

def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None or row["value"] is None:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return row["value"]


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def delete_setting(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM settings WHERE key=?", (key,))


# ---------------------------------------------------------------- helpers

def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def get_source_status(conn: sqlite3.Connection, source: str) -> dict:
    row = conn.execute("SELECT * FROM source_status WHERE source=?", (source,)).fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO source_status(source) VALUES(?)", (source,))
        row = conn.execute("SELECT * FROM source_status WHERE source=?", (source,)).fetchone()
    return dict(row)


def update_source_status(conn: sqlite3.Connection, source: str, **fields: Any) -> None:
    get_source_status(conn, source)
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE source_status SET {cols} WHERE source=?", (*fields.values(), source))
