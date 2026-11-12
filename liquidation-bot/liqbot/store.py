"""SQLite storage shared by the bot (writer) and the monitor (reader)."""
from __future__ import annotations

import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS borrowers (
    address TEXT PRIMARY KEY, hf REAL, coll_base REAL, debt_base REAL, updated REAL);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, mode TEXT, block INTEGER, user TEXT,
    collateral TEXT, debt TEXT, debt_to_cover TEXT, route TEXT,
    profit_base REAL, gas_base REAL, net_base REAL,
    status TEXT, tx_hash TEXT, lead_blocks INTEGER, rival_block INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS market_liqs (
    tx_hash TEXT, log_index INTEGER, block INTEGER, user TEXT, collateral TEXT, debt TEXT,
    liquidator TEXT, PRIMARY KEY (tx_hash, log_index));
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, msg TEXT);
"""

# Trades with these statuses count toward the profit-and-loss balance.
REALISED = ("paper", "success", "reverted", "capturable")


class Store:
    def __init__(self, path: str, readonly: bool = False):
        if readonly:
            self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        else:
            self.db = sqlite3.connect(path, check_same_thread=False)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(SCHEMA)
        self.db.row_factory = sqlite3.Row

    # --- key/value -------------------------------------------------------
    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value)))
        self.db.commit()

    # --- borrowers -------------------------------------------------------
    def add_borrowers(self, addresses) -> None:
        self.db.executemany("INSERT OR IGNORE INTO borrowers (address) VALUES (?)", [(a,) for a in addresses])
        self.db.commit()

    def borrowers(self, below_hf: float | None = None) -> list[str]:
        if below_hf is None:
            rows = self.db.execute("SELECT address FROM borrowers")
        else:
            # hf IS NULL = newly discovered, not checked yet
            rows = self.db.execute("SELECT address FROM borrowers WHERE hf < ? OR hf IS NULL", (below_hf,))
        return [r["address"] for r in rows]

    def update_health(self, rows: list[tuple[str, float, float, float]]) -> None:
        now = time.time()
        self.db.executemany(
            "UPDATE borrowers SET hf=?, coll_base=?, debt_base=?, updated=? WHERE address=?",
            [(hf, c, d, now, a) for a, hf, c, d in rows],
        )
        self.db.commit()

    def remove_borrowers(self, addresses) -> None:
        self.db.executemany("DELETE FROM borrowers WHERE address=?", [(a,) for a in addresses])
        self.db.commit()

    # --- trades and market activity -------------------------------------
    def add_trade(self, **row) -> int:
        row.setdefault("ts", time.time())
        cols = ",".join(row)
        cur = self.db.execute(f"INSERT INTO trades ({cols}) VALUES ({','.join('?' * len(row))})", list(row.values()))
        self.db.commit()
        return cur.lastrowid

    def add_market_liq(self, **row) -> None:
        cols = ",".join(row)
        self.db.execute(
            f"INSERT OR IGNORE INTO market_liqs ({cols}) VALUES ({','.join('?' * len(row))})", list(row.values())
        )
        # If we paper-traded this borrower, note how many blocks later a rival really took it.
        self.db.execute(
            "UPDATE trades SET rival_block=?, lead_blocks=?-block "
            "WHERE mode='paper' AND user=? AND rival_block IS NULL AND block<=?",
            (row["block"], row["block"], row["user"], row["block"]),
        )
        self.db.commit()

    def clear_mode(self, mode: str) -> None:
        self.db.execute("DELETE FROM trades WHERE mode=?", (mode,))
        self.db.commit()

    def log(self, msg: str, level: str = "info") -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {level.upper():5} {msg}", flush=True)
        self.db.execute("INSERT INTO events (ts, level, msg) VALUES (?,?,?)", (time.time(), level, msg))
        self.db.execute("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - 2000")
        self.db.commit()

    # --- read side (monitor, summaries) ---------------------------------
    def summary(self, mode: str) -> dict:
        marks = ",".join("?" * len(REALISED))
        r = self.db.execute(
            f"SELECT COUNT(*) n, COALESCE(SUM(net_base),0) net, COALESCE(SUM(gas_base),0) gas "
            f"FROM trades WHERE mode=? AND status IN ({marks})",
            (mode, *REALISED),
        ).fetchone()
        by_status = {
            row["status"]: row["n"]
            for row in self.db.execute("SELECT status, COUNT(*) n FROM trades WHERE mode=? GROUP BY status", (mode,))
        }
        return {"trades": r["n"], "net": r["net"], "gas": r["gas"], "by_status": by_status}

    def rows(self, sql: str, args=()) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql, args)]
