"""SQLite store for incident data — instant reads, Metabase fetches in background."""
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path(__file__).parent.parent / "data.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db():
    """Create tables if they don't exist."""
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY,
        partner_id INTEGER,
        severity TEXT,
        size_bucket TEXT,
        duration_bucket TEXT,
        device_count INTEGER,
        status TEXT,
        duration_minutes INTEGER,
        first_fail_ist TEXT,
        created_ist TEXT,
        closed_ist TEXT,
        recovery_percentage REAL,
        fetched_at TEXT
    );
    CREATE TABLE IF NOT EXISTS devices (
        rowid INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_id INTEGER,
        device_id TEXT,
        device_status TEXT,
        device_down_ist TEXT,
        device_recovery_ist TEXT,
        lat REAL,
        lng REAL,
        address TEXT,
        locality TEXT,
        partner_id INTEGER,
        customer_mobile TEXT,
        customer_name TEXT,
        fetched_at TEXT
    );
    CREATE TABLE IF NOT EXISTS concurrent (
        incident_id INTEGER PRIMARY KEY,
        partner_id INTEGER,
        concurrent_count INTEGER,
        fetched_at TEXT
    );
    CREATE TABLE IF NOT EXISTS tickets (
        rowid INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_id INTEGER,
        kapture_ticket_id TEXT,
        ticket_title TEXT,
        ticket_time TEXT,
        ticket_mobile TEXT,
        fetched_at TEXT
    );
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_devices_incident ON devices(incident_id);
    CREATE INDEX IF NOT EXISTS idx_tickets_incident ON tickets(incident_id);
    CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_ist);
    """)
    c.commit()
    c.close()


def save_incidents(rows: list[dict]):
    """Upsert raw incident rows from Metabase."""
    c = _conn()
    now = datetime.utcnow().isoformat()
    for r in rows:
        c.execute("""
            INSERT OR REPLACE INTO incidents
            (id, partner_id, severity, size_bucket, duration_bucket, device_count,
             status, duration_minutes, first_fail_ist, created_ist, closed_ist,
             recovery_percentage, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r["ID"], r["PARTNER_ID"], r["SEVERITY"], r["SIZE_BUCKET"],
            r.get("DURATION_BUCKET"), r["DEVICE_COUNT"], r["STATUS"],
            r["DURATION_MINUTES"], str(r.get("FIRST_FAIL_IST") or ""),
            str(r.get("CREATED_IST") or ""), str(r.get("CLOSED_IST") or ""),
            r.get("RECOVERY_PERCENTAGE"), now,
        ))
    c.commit()
    c.close()


def save_devices(rows: list[dict]):
    """Save device rows. Deletes old devices for these incidents first."""
    if not rows:
        return
    c = _conn()
    now = datetime.utcnow().isoformat()
    incident_ids = list(set(r["INCIDENT_ID"] for r in rows))
    # Delete old device rows for these incidents
    for batch_start in range(0, len(incident_ids), 500):
        batch = incident_ids[batch_start:batch_start + 500]
        placeholders = ",".join("?" * len(batch))
        c.execute(f"DELETE FROM devices WHERE incident_id IN ({placeholders})", batch)
    for r in rows:
        c.execute("""
            INSERT INTO devices
            (incident_id, device_id, device_status, device_down_ist, device_recovery_ist,
             lat, lng, address, locality, partner_id, customer_mobile, customer_name, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r["INCIDENT_ID"], r["DEVICE_ID"], r.get("DEVICE_STATUS"),
            str(r.get("DEVICE_DOWN_IST") or ""), str(r.get("DEVICE_RECOVERY_IST") or ""),
            r.get("LAT"), r.get("LNG"), r.get("ADDRESS"), r.get("LOCALITY"),
            r.get("PARTNER_ID"), r.get("CUSTOMER_MOBILE"), r.get("CUSTOMER_NAME"), now,
        ))
    c.commit()
    c.close()


def save_concurrent(rows: dict[int, dict]):
    """Save concurrent count map."""
    c = _conn()
    now = datetime.utcnow().isoformat()
    for iid, r in rows.items():
        c.execute("""
            INSERT OR REPLACE INTO concurrent (incident_id, partner_id, concurrent_count, fetched_at)
            VALUES (?,?,?,?)
        """, (iid, r.get("PARTNER_ID"), r.get("CONCURRENT_COUNT", 0), now))
    c.commit()
    c.close()


def save_tickets(ticket_map: dict[int, list[dict]]):
    """Save ticket rows. Deletes old tickets for these incidents first."""
    if not ticket_map:
        return
    c = _conn()
    now = datetime.utcnow().isoformat()
    incident_ids = list(ticket_map.keys())
    for batch_start in range(0, len(incident_ids), 500):
        batch = incident_ids[batch_start:batch_start + 500]
        placeholders = ",".join("?" * len(batch))
        c.execute(f"DELETE FROM tickets WHERE incident_id IN ({placeholders})", batch)
    for iid, tix_list in ticket_map.items():
        for r in tix_list:
            c.execute("""
                INSERT INTO tickets
                (incident_id, kapture_ticket_id, ticket_title, ticket_time, ticket_mobile, fetched_at)
                VALUES (?,?,?,?,?,?)
            """, (
                r["INCIDENT_ID"], r.get("KAPTURE_TICKET_ID"), r.get("TICKET_TITLE"),
                str(r.get("TICKET_TIME") or ""), r.get("TICKET_MOBILE"), now,
            ))
    c.commit()
    c.close()


def set_meta(key: str, value: str):
    c = _conn()
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))
    c.commit()
    c.close()


def get_meta(key: str) -> str | None:
    c = _conn()
    row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    c.close()
    return row["value"] if row else None


# ── Read functions (instant, from SQLite) ──

def load_incidents(lookback_hours: int = 6) -> list[dict]:
    """Load incidents from SQLite for the lookback window."""
    c = _conn()
    cutoff = (datetime.utcnow() - timedelta(hours=lookback_hours)).isoformat()
    rows = c.execute("""
        SELECT id as ID, partner_id as PARTNER_ID, severity as SEVERITY,
               size_bucket as SIZE_BUCKET, device_count as DEVICE_COUNT,
               status as STATUS, duration_minutes as DURATION_MINUTES,
               first_fail_ist as FIRST_FAIL_IST, created_ist as CREATED_IST,
               closed_ist as CLOSED_IST
        FROM incidents
        ORDER BY created_ist DESC
    """).fetchall()
    c.close()
    return [dict(r) for r in rows]


def load_devices(incident_ids: list[int]) -> list[dict]:
    """Load devices from SQLite."""
    if not incident_ids:
        return []
    c = _conn()
    all_rows = []
    for batch_start in range(0, len(incident_ids), 500):
        batch = incident_ids[batch_start:batch_start + 500]
        placeholders = ",".join("?" * len(batch))
        rows = c.execute(f"""
            SELECT incident_id as INCIDENT_ID, device_id as DEVICE_ID,
                   device_status as DEVICE_STATUS, device_down_ist as DEVICE_DOWN_IST,
                   device_recovery_ist as DEVICE_RECOVERY_IST,
                   lat as LAT, lng as LNG, address as ADDRESS, locality as LOCALITY,
                   partner_id as PARTNER_ID, customer_mobile as CUSTOMER_MOBILE,
                   customer_name as CUSTOMER_NAME
            FROM devices WHERE incident_id IN ({placeholders})
        """, batch).fetchall()
        all_rows.extend(dict(r) for r in rows)
    c.close()
    return all_rows


def load_concurrent(incident_ids: list[int]) -> dict[int, dict]:
    """Load concurrent counts from SQLite."""
    if not incident_ids:
        return {}
    c = _conn()
    result = {}
    for batch_start in range(0, len(incident_ids), 500):
        batch = incident_ids[batch_start:batch_start + 500]
        placeholders = ",".join("?" * len(batch))
        rows = c.execute(f"""
            SELECT incident_id as INCIDENT_ID, partner_id as PARTNER_ID,
                   concurrent_count as CONCURRENT_COUNT
            FROM concurrent WHERE incident_id IN ({placeholders})
        """, batch).fetchall()
        for r in rows:
            result[r["INCIDENT_ID"]] = dict(r)
    c.close()
    return result


def load_tickets(incident_ids: list[int]) -> dict[int, list[dict]]:
    """Load tickets from SQLite."""
    if not incident_ids:
        return {}
    c = _conn()
    result: dict[int, list[dict]] = {}
    for batch_start in range(0, len(incident_ids), 500):
        batch = incident_ids[batch_start:batch_start + 500]
        placeholders = ",".join("?" * len(batch))
        rows = c.execute(f"""
            SELECT incident_id as INCIDENT_ID, kapture_ticket_id as KAPTURE_TICKET_ID,
                   ticket_title as TICKET_TITLE, ticket_time as TICKET_TIME,
                   ticket_mobile as TICKET_MOBILE
            FROM tickets WHERE incident_id IN ({placeholders})
        """, batch).fetchall()
        for r in rows:
            iid = r["INCIDENT_ID"]
            result.setdefault(iid, []).append(dict(r))
    c.close()
    return result


def has_recent_data(max_age_minutes: int = 30) -> bool:
    """Check if we have data fetched within the last N minutes."""
    lr = get_meta("last_refresh")
    if not lr:
        return False
    try:
        last = datetime.fromisoformat(lr)
        age = (datetime.utcnow() - last).total_seconds() / 60
        return age < max_age_minutes
    except Exception:
        return False


# Init on import
init_db()
