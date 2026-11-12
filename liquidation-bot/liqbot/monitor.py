"""Read-only web dashboard. Reads the bot's SQLite file; never touches keys or the chain.

Binds to 127.0.0.1 by default. To reach it from your other devices over Tailscale:
    tailscale serve --bg 8787        (HTTPS inside your tailnet only)
Do not use `tailscale funnel`: that would publish it to the whole internet.
"""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .store import REALISED, Store

PAGE = Path(__file__).with_name("monitor.html")
MODES = ("paper", "live", "backtest")


def state(store: Store, mode: str) -> dict:
    status = store.get("status", {}) or {}
    start = status.get("start_balance", 550.0)
    summary = store.summary(mode)
    marks = ",".join("?" * len(REALISED))
    curve, total = [], 0.0
    for r in store.rows(
        f"SELECT ts, block, net_base FROM trades WHERE mode=? AND status IN ({marks}) ORDER BY id", (mode, *REALISED)
    ):
        total += r["net_base"] or 0
        curve.append({"ts": r["ts"], "block": r["block"], "net": r["net_base"] or 0, "cum": total})
    rivals = store.rows(
        "SELECT COUNT(*) n, COUNT(rival_block) taken, AVG(lead_blocks) lead FROM trades WHERE mode=? "
        "AND status IN ('paper','capturable')", (mode,)
    )[0]
    market = store.rows("SELECT COUNT(*) n, COUNT(DISTINCT liquidator) who FROM market_liqs")[0]
    return {
        "now": time.time(),
        "mode": mode,
        "status": status,
        "summary": {**summary, "start": start, "balance": start + summary["net"]},
        "rivals": rivals,
        "market": market,
        "curve": curve,
        "trades": store.rows("SELECT * FROM trades WHERE mode=? ORDER BY id DESC LIMIT 100", (mode,)),
        "risky": store.rows(
            "SELECT address, hf, coll_base, debt_base FROM borrowers WHERE hf IS NOT NULL ORDER BY hf LIMIT 15"
        ),
        "market_liqs": store.rows("SELECT * FROM market_liqs ORDER BY block DESC LIMIT 15"),
        "events": store.rows("SELECT ts, level, msg FROM events ORDER BY id DESC LIMIT 60"),
    }


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/state":
                mode = parse_qs(url.query).get("mode", ["paper"])[0]
                if mode not in MODES:
                    return self._send(400, b"bad mode", "text/plain")
                try:
                    store = Store(db_path, readonly=True)
                    body = json.dumps(state(store, mode)).encode()
                    store.db.close()
                except Exception as exc:  # database not created yet, etc.
                    return self._send(503, json.dumps({"error": str(exc)}).encode(), "application/json")
                self._send(200, body, "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *args):  # keep the terminal quiet
            pass

    return ThreadingHTTPServer((host, port), Handler)
