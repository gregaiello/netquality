"""Local result history in SQLite (~/.netquality/history.db)."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path.home() / ".netquality" / "history.db"

COLUMNS = ["timestamp", "down", "up", "ping", "jitter", "loss", "bloat_dl", "bloat_ul",
           "grade", "medium", "server", "isp_ip"]


class History:
    def __init__(self, path: Path = DEFAULT_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("""CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, down REAL, up REAL, ping REAL,
            jitter REAL, loss REAL, bloat_dl REAL, bloat_ul REAL, grade TEXT, medium TEXT,
            server TEXT, isp_ip TEXT, raw TEXT)""")
        self.db.commit()

    def add(self, r: dict) -> int:
        lat = r.get("latency") or {}
        http = lat.get("http") or {}
        icmp = lat.get("icmp") or {}
        dl, ul = r.get("download") or {}, r.get("upload") or {}
        grades = [g for g in (dl.get("bufferbloat_grade"), ul.get("bufferbloat_grade")) if g and g != "?"]
        order = ["A+", "A", "B", "C", "D", "F"]
        grade = max(grades, key=order.index) if grades else "?"
        meta = r.get("meta") or {}
        cur = self.db.execute(
            "INSERT INTO results (timestamp, down, up, ping, jitter, loss, bloat_dl, bloat_ul, grade, "
            "medium, server, isp_ip, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r.get("timestamp"), dl.get("mbps"), ul.get("mbps"), http.get("median"),
             icmp.get("jitter_ms", http.get("jitter_ms")), icmp.get("loss_pct"),
             dl.get("bufferbloat_ms"), ul.get("bufferbloat_ms"), grade,
             (r.get("link") or {}).get("medium"), meta.get("colo"), meta.get("ip"), json.dumps(r)))
        self.db.commit()
        return cur.lastrowid

    def rows(self) -> list[dict]:
        cur = self.db.execute(f"SELECT id, {', '.join(COLUMNS)} FROM results ORDER BY id")
        return [dict(zip(["id", *COLUMNS], row)) for row in cur.fetchall()]

    def raw(self, row_id: int) -> dict | None:
        row = self.db.execute("SELECT raw FROM results WHERE id=?", (row_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def delete(self, row_id: int):
        self.db.execute("DELETE FROM results WHERE id=?", (row_id,))
        self.db.commit()

    def export_csv(self, path: str):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["id", *COLUMNS])
            w.writeheader()
            w.writerows(self.rows())
