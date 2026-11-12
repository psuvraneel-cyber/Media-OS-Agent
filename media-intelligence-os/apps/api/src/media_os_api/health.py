"""Minimal Phase 1 baseline HTTP health service.

Exposes only `/health` using Python's standard library so Phase 1 can validate
Docker image builds, container health checks, loopback port binding, and
reproducible restarts without prematurely adding Phase 3+ backend services.
"""

from __future__ import annotations

import json
import os
import signal
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import FrameType
from typing import Any


@dataclass(frozen=True)
class BaselineConfig:
    """Configuration for the minimal Phase 1 baseline health server."""

    host: str
    port: int
    app_env: str
    service_name: str = "media-os-baseline-api"
    phase: str = "phase-1-baseline"

    @classmethod
    def from_env(cls) -> BaselineConfig:
        """Load configuration from environment variables with safe loopback defaults."""
        host = os.environ.get("API_HOST", "127.0.0.1").strip() or "127.0.0.1"
        raw_port = os.environ.get("API_PORT", "8000").strip() or "8000"
        port = int(raw_port)
        if not 1 <= port <= 65535:
            raise ValueError(f"API_PORT must be between 1 and 65535, got {port}")
        app_env = os.environ.get("APP_ENV", "development").strip() or "development"
        return cls(host=host, port=port, app_env=app_env)


def build_health_payload(config: BaselineConfig) -> dict[str, Any]:
    """Build deterministic JSON response payload for the `/health` endpoint."""
    return {
        "status": "ok",
        "service": config.service_name,
        "phase": config.phase,
        "environment": config.app_env,
        "timestamp_utc": datetime.now(tz=UTC).isoformat(),
    }


def make_handler(config: BaselineConfig) -> type[BaseHTTPRequestHandler]:
    """Create a request handler class bound to the provided baseline config."""

    class BaselineHealthHandler(BaseHTTPRequestHandler):
        """Serve `/health` and reject all undefined routes cleanly."""

        server_version = "MediaOSBaseline/0.1"

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._write_json(HTTPStatus.OK, build_health_payload(config))
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"detail": "Not Found"})

        def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # Emit compact structured line to stdout without logging headers/secrets.
            message = format % args
            print(f"[baseline-api] {self.address_string()} - {message}", flush=True)

    return BaselineHealthHandler


def create_server(config: BaselineConfig) -> ThreadingHTTPServer:
    """Instantiate a configured HTTP server instance."""
    ThreadingHTTPServer.allow_reuse_address = True
    return ThreadingHTTPServer((config.host, config.port), make_handler(config))


def main() -> None:
    """Run the baseline health server until interrupted by SIGINT or SIGTERM."""
    config = BaselineConfig.from_env()
    server = create_server(config)

    def _shutdown_handler(signum: int, _frame: FrameType | None) -> None:
        print(f"[baseline-api] received signal {signum}, shutting down", flush=True)
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    try:
        print(
            f"[baseline-api] listening on {config.host}:{config.port} ({config.app_env})",
            flush=True,
        )
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
