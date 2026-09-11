"""SQLite store for raw messages and parsed events."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path

from .parser import Event, Parser

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "events.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, text TEXT NOT NULL, parsed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, msg_id INTEGER NOT NULL, ts TEXT NOT NULL, raion TEXT NOT NULL,
  kind TEXT NOT NULL, role TEXT NOT NULL, weight REAL NOT NULL, count INTEGER NOT NULL, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_msg ON events(msg_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class Store:
    def __init__(self, path: Path = DB_PATH, parser: Parser | None = None):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.parser = parser or Parser()

    def last_msg_id(self) -> int:
        row = self.conn.execute("SELECT MAX(id) AS m FROM messages").fetchone()
        return row["m"] or 0

    def add_message(self, msg_id: int, ts: str, text: str) -> list[Event]:
        """Insert a message (idempotent) and its parsed events. Returns new events."""
        cur = self.conn.execute("INSERT OR IGNORE INTO messages(id, ts, text) VALUES (?,?,?)", (msg_id, ts, text))
        if cur.rowcount == 0:
            return []
        events = self.parser.parse(msg_id, ts, text)
        self._insert_events(events)
        self.conn.execute("UPDATE messages SET parsed=1 WHERE id=?", (msg_id,))
        self.conn.commit()
        return events

    def _insert_events(self, events: list[Event]):
        self.conn.executemany(
            "INSERT INTO events(msg_id, ts, raion, kind, role, weight, count, text) VALUES (?,?,?,?,?,?,?,?)",
            [(e.msg_id, e.ts, e.raion, e.kind, e.role, e.weight, e.count, e.text) for e in events],
        )

    def reparse_all(self) -> int:
        """Re-run the parser over every stored message (after parser changes)."""
        self.conn.execute("DELETE FROM events")
        n = 0
        for row in self.conn.execute("SELECT id, ts, text FROM messages ORDER BY id"):
            evs = self.parser.parse(row["id"], row["ts"], row["text"])
            self._insert_events(evs)
            n += len(evs)
        self.conn.commit()
        return n

    def events_since(self, since_iso: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, msg_id, ts, raion, kind, role, weight, count, text FROM events WHERE ts >= ? ORDER BY ts, id",
            (since_iso,),
        ).fetchall()
        return [dict(r) for r in rows]

    def messages_since(self, since_iso: str) -> list[dict]:
        rows = self.conn.execute("SELECT id, ts, text FROM messages WHERE ts >= ? ORDER BY id", (since_iso,)).fetchall()
        return [dict(r) for r in rows]

    def set_meta(self, key: str, value: str):
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, value))
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


if __name__ == "__main__":  # load the sample corpus:  python -m ingest.store data/samples.jsonl
    import json, sys
    st = Store()
    n = 0
    for line in open(sys.argv[1]):
        r = json.loads(line)
        if r["ts"]:
            n += len(st.add_message(r["id"], r["ts"], r["text"]))
    print("events inserted", n, "last id", st.last_msg_id())
