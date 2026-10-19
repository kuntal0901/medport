"""Local SQLite store. Everything lives in one data folder on the user's machine."""
import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.environ.get("MEDPORT_DATA", Path.home() / ".medport")).expanduser()
DOCS_DIR = DATA_DIR / "documents"
DB_PATH = DATA_DIR / "medport.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    mime TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    report_date TEXT,
    title TEXT,
    lab TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    engine TEXT,
    note TEXT,
    sha256 TEXT
);
CREATE TABLE IF NOT EXISTS pages (
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    text TEXT NOT NULL,
    ocr INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (doc_id, page)
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page INTEGER,
    test TEXT NOT NULL,
    canonical TEXT,
    loinc TEXT,
    value REAL,
    value_text TEXT NOT NULL,
    unit TEXT,
    ref_low REAL,
    ref_high REAL,
    ref_text TEXT,
    flag TEXT,
    date TEXT,
    verified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('condition', 'medication', 'allergy')),
    name TEXT NOT NULL,
    detail TEXT,
    date TEXT,
    page INTEGER,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS pair_codes (
    code TEXT PRIMARY KEY,
    expires REAL NOT NULL,
    device_id INTEGER
);
CREATE TABLE IF NOT EXISTS profile (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
    if "sha256" not in cols:  # databases from before duplicate detection
        conn.execute("ALTER TABLE documents ADD COLUMN sha256 TEXT")
    # who the report is about, and whether that is the profile's owner:
    # patient_check is match / mismatch (kept out of the portfolio until the user decides) / accepted / unknown
    for col in ("patient_name", "patient_age", "patient_sex", "patient_dob", "patient_check", "patient_note"):
        if col not in cols:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} TEXT")
    return conn


def rows(conn, sql, *args):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def get_profile(conn) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM profile")}


def set_profile(conn, data: dict):
    for k, v in data.items():
        conn.execute("INSERT OR REPLACE INTO profile (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
