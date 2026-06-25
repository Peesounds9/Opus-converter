"""Tiny stdlib HTTP server for platform healthchecks.

The Telegram bot itself is a long-running worker that doesn't speak HTTP.
Hosts like Railway, Render, Fly, etc. expect something to answer on a port
so they know the process is alive. This module exposes a one-endpoint HTTP
server that reports the bot's last rate-refresh time.

It uses only the standard library so we don't add aiohttp / starlette to
the dependency tree just for one route.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from config import SETTINGS
import rates as rates_mod

log = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    server_version = "OpusHealth/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        log.debug("healthcheck: " + format, *args)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in ("/", "/health", "/healthz", "/status"):
            self._json(404, {"error": "not found"})
            return

        rate_map, meta = rates_mod.load_cached_rates()
        fetched_at = float(meta.get("fetched_at") or 0)
        age_seconds = (time.time() - fetched_at) if fetched_at else None

        payload = {
            "status": "ok" if rate_map else "degraded",
            "currencies": len(rate_map),
            "base": meta.get("base", SETTINGS.base_currency),
            "provider": meta.get("provider"),
            "fetched_at": fetched_at or None,
            "age_seconds": age_seconds,
            "refresh_minutes": SETTINGS.refresh_minutes,
        }
        # Return 200 even if degraded — the *process* is healthy, the data
        # may just be stale. Railway/Render only care about the HTTP response.
        self._json(200, payload)


def start_healthcheck_server() -> ThreadingHTTPServer | None:
    """Start the healthcheck server in a background thread.

    Returns the server instance, or None if no port was configured (so we
    don't bind to 8080 during local dev or unit tests).
    """
    port = SETTINGS.healthcheck_port
    if not port:
        log.info("Healthcheck server disabled (PORT not set).")
        return None

    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="opus-healthcheck",
        daemon=True,
    )
    thread.start()
    log.info("Healthcheck server listening on 0.0.0.0:%d", port)
    return server
