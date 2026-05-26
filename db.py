import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".omron-bp" / "records.db"


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT,
            systolic    INTEGER NOT NULL,
            diastolic   INTEGER NOT NULL,
            mean_ap     INTEGER,
            pulse       INTEGER,
            user_id     INTEGER DEFAULT 1,
            unit        TEXT DEFAULT 'mmHg',
            synced_at   TEXT NOT NULL,
            UNIQUE(timestamp, systolic, diastolic, pulse)
        )
    """)
    conn.commit()
    return conn


def insert_measurement(conn: sqlite3.Connection, m: dict) -> bool:
    """Insert measurement; return True if new, False if duplicate."""
    ts = m.get("timestamp")
    ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts) if ts else None
    try:
        conn.execute(
            """INSERT INTO measurements
               (timestamp, systolic, diastolic, mean_ap, pulse, user_id, unit, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ts_str,
                m["systolic"],
                m["diastolic"],
                m.get("mean_arterial"),
                m.get("pulse"),
                m.get("user", 1),
                m.get("unit", "mmHg"),
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def fetch_all(conn: sqlite3.Connection, user_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM measurements"
    params: tuple = ()
    if user_id is not None:
        query += " WHERE user_id = ?"
        params = (user_id,)
    query += " ORDER BY timestamp DESC"
    cur = conn.execute(query, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_stats(conn: sqlite3.Connection, user_id: int | None = None) -> dict:
    where = "WHERE user_id = ?" if user_id else ""
    params = (user_id,) if user_id else ()
    row = conn.execute(
        f"""SELECT
            COUNT(*),
            ROUND(AVG(systolic)),  MIN(systolic),  MAX(systolic),
            ROUND(AVG(diastolic)), MIN(diastolic), MAX(diastolic),
            ROUND(AVG(pulse)),     MIN(pulse),      MAX(pulse)
            FROM measurements {where}""",
        params,
    ).fetchone()
    return {
        "count": row[0],
        "systolic":  {"avg": row[1],  "min": row[2],  "max": row[3]},
        "diastolic": {"avg": row[4],  "min": row[5],  "max": row[6]},
        "pulse":     {"avg": row[7],  "min": row[8],  "max": row[9]},
    }
