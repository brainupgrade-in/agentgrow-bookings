"""SQLite schema and small helpers. Times are local (business timezone) ISO strings 'YYYY-MM-DDTHH:MM'."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'clinic' CHECK (kind IN ('clinic')),  -- picks the domain rules in the prompt (AgentGrow leads: later)
    owner_wa TEXT NOT NULL,              -- owner's WhatsApp id (digits, no +)
    open_time TEXT NOT NULL,             -- 'HH:MM'
    close_time TEXT NOT NULL,
    closed_weekdays TEXT NOT NULL DEFAULT '',  -- comma list, Monday=0
    chairs INTEGER NOT NULL DEFAULT 1,       -- parallel capacity: practitioners on duty (bookings.chair = which one)
    slot_step_min INTEGER NOT NULL DEFAULT 30,
    address TEXT, website TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    name TEXT NOT NULL,
    price_inr INTEGER NOT NULL,
    duration_min INTEGER NOT NULL,
    lapse_weeks INTEGER,                 -- re-booking nudge after this many weeks; NULL = never
    active INTEGER NOT NULL DEFAULT 1    -- 0 = removed from the menu (old bookings still point at it)
);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    wa_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    last_nudge_at TEXT,
    UNIQUE (business_id, wa_id)
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    service_id INTEGER NOT NULL REFERENCES services(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    chair INTEGER NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    price_inr INTEGER NOT NULL,          -- snapshot at booking time
    status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'cancelled', 'done', 'no_show')),
    source TEXT NOT NULL DEFAULT 'direct' CHECK (source IN ('direct', 'waitlist', 'nudge')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_booking_per_chair_start
    ON bookings (business_id, chair, start_at) WHERE status = 'booked';
CREATE TABLE IF NOT EXISTS waitlist (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    service_id INTEGER NOT NULL REFERENCES services(id),
    day TEXT NOT NULL,                   -- 'YYYY-MM-DD'
    status TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'offered', 'booked', 'expired')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS owner_requests (   -- ask_owner questions waiting for the owner's answer
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    customer_wa TEXT NOT NULL,
    customer_name TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL,
    answer TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'relayed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS messages (
    msg_id TEXT PRIMARY KEY,             -- WhatsApp message id; doubles as the idempotency key
    business_id INTEGER,
    wa_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    body TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    business_id INTEGER,
    wa_id TEXT,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS recoveries (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    kind TEXT NOT NULL CHECK (kind IN ('waitlist_fill', 'nudge', 'deposit')),
    amount_inr INTEGER NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


MIGRATIONS = [
    ("services", "active", "INTEGER NOT NULL DEFAULT 1"),
    ("businesses", "kind", "TEXT NOT NULL DEFAULT 'clinic'"),
    ("businesses", "last_report_at", "TEXT"),        # weekly ₹ report, last sent
    ("waitlist", "offered_start", "TEXT"),           # the freed slot offered to this entry
    ("waitlist", "notified_at", "TEXT"),
    ("bookings", "deposit_status", "TEXT"),          # NULL = none needed, 'pending', 'paid'
    ("bookings", "deposit_link_id", "TEXT"),
    ("bookings", "deposit_due", "TEXT"),             # unpaid by then (business-local time) -> the hold is released
]


def connect(path: str | Path) -> sqlite3.Connection:
    # isolation_level=None: we issue BEGIN IMMEDIATE ourselves where a write must be race-free.
    conn = sqlite3.connect(path, isolation_level=None, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for table, column, ddl in MIGRATIONS:  # columns added after the first DBs were created
        if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def add_business(conn, *, name, owner_wa, open_time, close_time, chairs=1, closed_weekdays="", slot_step_min=30, kind="clinic") -> int:
    cur = conn.execute(
        "INSERT INTO businesses (name, kind, owner_wa, open_time, close_time, chairs, closed_weekdays, slot_step_min)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, kind, owner_wa, open_time, close_time, chairs, closed_weekdays, slot_step_min),
    )
    return cur.lastrowid


def add_service(conn, business_id, *, name, price_inr, duration_min, lapse_weeks=None) -> int:
    cur = conn.execute(
        "INSERT INTO services (business_id, name, price_inr, duration_min, lapse_weeks) VALUES (?, ?, ?, ?, ?)",
        (business_id, name, price_inr, duration_min, lapse_weeks),
    )
    return cur.lastrowid
