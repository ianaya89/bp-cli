import json
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
    cols = [r[1] for r in conn.execute("PRAGMA table_info(measurements)")]
    if "tags" not in cols:
        conn.execute("ALTER TABLE measurements ADD COLUMN tags TEXT")
    conn.commit()
    return conn


def _row_to_dict(cols, row) -> dict:
    d = dict(zip(cols, row))
    d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
    return d


def insert_measurement(conn: sqlite3.Connection, m: dict) -> bool:
    """Insert measurement; return True if new, False if duplicate."""
    ts = m.get("timestamp")
    ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts) if ts else None
    tags_str = json.dumps(m["tags"]) if m.get("tags") else None

    # For null-timestamp records, dedup by values — SQLite UNIQUE treats NULLs as distinct
    if ts_str is None:
        existing = conn.execute(
            """SELECT 1 FROM measurements
               WHERE timestamp IS NULL
               AND systolic=? AND diastolic=? AND pulse=? AND user_id=?""",
            (m["systolic"], m["diastolic"], m.get("pulse"), m.get("user", 1)),
        ).fetchone()
        if existing:
            return False

    try:
        conn.execute(
            """INSERT INTO measurements
               (timestamp, systolic, diastolic, mean_ap, pulse, user_id, unit, synced_at, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ts_str,
                m["systolic"],
                m["diastolic"],
                m.get("mean_arterial"),
                m.get("pulse"),
                m.get("user", 1),
                m.get("unit", "mmHg"),
                datetime.now().isoformat(),
                tags_str,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def fetch_by_id(conn: sqlite3.Connection, record_id: int) -> dict | None:
    cur = conn.execute("SELECT * FROM measurements WHERE id = ?", (record_id,))
    cols = [c[0] for c in cur.description]
    row = cur.fetchone()
    return _row_to_dict(cols, row) if row else None


def update_measurement(conn: sqlite3.Connection, record_id: int, fields: dict) -> bool:
    if not fields:
        return False
    allowed = {"timestamp", "systolic", "diastolic", "pulse", "user_id", "tags"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    if "tags" in updates:
        updates["tags"] = json.dumps(updates["tags"]) if updates["tags"] else None
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE measurements SET {set_clause} WHERE id = ?",
        (*updates.values(), record_id),
    )
    conn.commit()
    return conn.total_changes > 0


def remove_tag_from_all(conn: sqlite3.Connection, tag: str) -> int:
    """Strip a tag from every record that carries it. Returns count changed."""
    rows = conn.execute(
        "SELECT id, tags FROM measurements WHERE tags LIKE ?", (f'%"{tag}"%',)
    ).fetchall()
    n = 0
    for rid, raw in rows:
        lst = json.loads(raw) if raw else []
        if tag in lst:
            lst.remove(tag)
            conn.execute(
                "UPDATE measurements SET tags = ? WHERE id = ?",
                (json.dumps(lst) if lst else None, rid),
            )
            n += 1
    conn.commit()
    return n


def delete_measurement(conn: sqlite3.Connection, record_id: int) -> bool:
    conn.execute("DELETE FROM measurements WHERE id = ?", (record_id,))
    conn.commit()
    return conn.total_changes > 0


def _range_where(user_id, since, until, tag=None):
    """Build a shared WHERE clause + params for user/date/tag filtering."""
    clauses, params = [], []
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    if since is not None:
        clauses.append("timestamp >= ?")
        params.append(since)
    if until is not None:
        clauses.append("timestamp <= ?")
        params.append(until)
    if tag:
        clauses.append("tags LIKE ?")
        params.append(f'%"{tag}"%')  # tags stored as JSON array, no spaces
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, tuple(params)


def fetch_all(
    conn: sqlite3.Connection,
    user_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
    tag: str | None = None,
) -> list[dict]:
    where, params = _range_where(user_id, since, until, tag)
    query = f"SELECT * FROM measurements {where} ORDER BY timestamp DESC"
    cur = conn.execute(query, params)
    cols = [c[0] for c in cur.description]
    return [_row_to_dict(cols, row) for row in cur.fetchall()]


def fetch_stats(
    conn: sqlite3.Connection,
    user_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
    tag: str | None = None,
) -> dict:
    where, params = _range_where(user_id, since, until, tag)
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


def _parse_ts(r):
    t = r.get("timestamp")
    try:
        return datetime.fromisoformat(t) if t else None
    except (ValueError, TypeError):
        return None


def group_sessions(records: list[dict], window_minutes: int = 5) -> list[dict]:
    """
    Group readings taken within `window_minutes` of each other into one session
    (Omron devices store several readings per sitting).

    Returns groups (newest first), each:
      {"lead": <lowest reading>, "members": [recs newest-first], "count": n}
    The lead is the lowest reading (systolic, then diastolic, then pulse) — the
    clinically recommended value to record from a multi-reading sitting.
    Records with no timestamp form their own single-member group.
    """
    from datetime import timedelta

    dated = sorted((r for r in records if _parse_ts(r)), key=_parse_ts)
    undated = [r for r in records if not _parse_ts(r)]

    win = timedelta(minutes=window_minutes)
    raw_groups: list[list[dict]] = []
    cur: list[dict] = []
    for r in dated:
        if cur and _parse_ts(r) - _parse_ts(cur[-1]) <= win:
            cur.append(r)
        else:
            if cur:
                raw_groups.append(cur)
            cur = [r]
    if cur:
        raw_groups.append(cur)
    raw_groups.extend([r] for r in undated)

    out = []
    for g in raw_groups:
        lead = min(g, key=lambda r: (r["systolic"], r["diastolic"], r.get("pulse") or 9999))
        members = sorted(g, key=lambda r: _parse_ts(r) or datetime.min, reverse=True)
        out.append({"lead": lead, "members": members, "count": len(g)})

    out.sort(key=lambda grp: _parse_ts(grp["lead"]) or datetime.min, reverse=True)
    return out
