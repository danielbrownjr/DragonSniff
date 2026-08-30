"""Loopback-only DragonSniff application server."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
import json
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from .churn import ChurnConfig, ChurnRunner
from .observer import Observer
from .target import TargetValidationError, parse_target


MAX_LOCAL_REQUEST_BYTES = 16_384
LOCAL_POST_PATHS = {
    "/local/v1/session/start",
    "/local/v1/session/stop",
    "/local/v1/session/refresh",
    "/local/v1/session/reconnect-events",
    "/local/v1/session/stop-events",
    "/local/v1/churn/start",
    "/local/v1/churn/stop",
}
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/payload.js": ("payload.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class SessionManager:
    def __init__(self) -> None:
        self._observer: Observer | None = None
        self._churn: ChurnRunner | None = None
        self._lock = Lock()

    def start(self, target_value: str) -> dict[str, Any]:
        target = parse_target(target_value)
        with self._lock:
            previous = self._observer
            churn = self._churn
        if churn is not None and churn.snapshot()["state"] in {"running", "stopping"}:
            raise RuntimeError("a churn run is active; stop it before normal observation")
        if previous is not None and not previous.stop():
            raise RuntimeError("the previous observation session is still stopping")
        observer = Observer(target)
        with self._lock:
            self._observer = observer
            self._churn = None
        observer.start()
        return observer.snapshot()

    def stop(self) -> tuple[bool, dict[str, Any]]:
        observer = self.current()
        if observer is None:
            return True, self.empty_snapshot()
        completed = observer.stop()
        return completed, observer.snapshot()

    def refresh(self) -> tuple[bool, dict[str, Any]]:
        observer = self.current()
        if observer is None:
            raise RuntimeError("no observation session is active")
        started = observer.refresh()
        return started, observer.snapshot()

    def reconnect(self) -> tuple[bool, dict[str, Any]]:
        observer = self.current()
        if observer is None:
            raise RuntimeError("no observation session is active")
        started = observer.reconnect_events()
        return started, observer.snapshot()

    def stop_events(self) -> tuple[bool, dict[str, Any]]:
        observer = self.current()
        if observer is None:
            raise RuntimeError("no observation session is active")
        stopped = observer.stop_events()
        return stopped, observer.snapshot()

    def start_churn(
        self, target_value: str, configuration: object
    ) -> dict[str, Any]:
        target = parse_target(target_value)
        config = ChurnConfig.from_value(configuration)
        with self._lock:
            observer = self._observer
            previous = self._churn
        if observer is not None and observer.snapshot()["session_state"] != "stopped":
            raise RuntimeError("normal observation is active; stop it before starting churn")
        if previous is not None and previous.snapshot()["state"] in {"running", "stopping"}:
            raise RuntimeError("a churn run is already active")
        churn = ChurnRunner(target, config)
        with self._lock:
            self._observer = None
            self._churn = churn
        churn.start()
        return self.snapshot()

    def stop_churn(self) -> tuple[bool, dict[str, Any]]:
        churn = self.current_churn()
        if churn is None:
            raise RuntimeError("no churn run is active")
        completed = churn.stop()
        return completed, self.snapshot()

    def current(self) -> Observer | None:
        with self._lock:
            return self._observer

    def current_churn(self) -> ChurnRunner | None:
        with self._lock:
            return self._churn

    def snapshot(self) -> dict[str, Any]:
        observer = self.current()
        churn = self.current_churn()
        if observer is not None:
            result = observer.snapshot()
            result["active_mode"] = "observation"
            result["churn"] = self.empty_churn_snapshot()
            return result
        result = self.empty_snapshot()
        if churn is not None:
            result["active_mode"] = "churn"
            result["churn"] = churn.snapshot()
            result["target"] = result["churn"]["target"]
            result["recorder"] = result["churn"]["recorder"]
            result["recent_records"] = result["churn"]["recent_records"]
            result["limits"]["active_device_connections"] = result["churn"][
                "active_device_connections"
            ]
            result["limits"]["device_connection_limit"] = result["churn"][
                "device_connection_limit"
            ]
        return result

    def export_jsonl(self) -> str:
        observer = self.current()
        churn = self.current_churn()
        if churn is not None:
            return churn.recorder.export_jsonl()
        return observer.recorder.export_jsonl() if observer is not None else ""

    @staticmethod
    def empty_churn_snapshot() -> dict[str, Any]:
        config = ChurnConfig()
        return {
            "state": "idle",
            "run_id": None,
            "target": None,
            "configuration": config.snapshot(),
            "profile": config.profile_name(),
            "profiles": config.profile_snapshots(),
            "bounds": config.bounds(),
            "current_cycle": 0,
            "total_cycles": config.cycles,
            "active_churn_connections": 0,
            "active_device_connections": 0,
            "device_connection_limit": 2,
            "successful_connections": 0,
            "rejected_connections": 0,
            "http_failures": 0,
            "transport_failures": 0,
            "local_resource_failures": 0,
            "remote_eof": 0,
            "events_observed": 0,
            "parse_failures": 0,
            "boot_id_changed": False,
            "boot_id_changes": [],
            "initial_boot_id": None,
            "latest_boot_id": None,
            "latest_health": None,
            "cycles": [],
            "cleanup_complete": True,
            "failure": None,
            "start_timestamp": None,
            "end_timestamp": None,
            "elapsed_ms": 0.0,
            "recorder": {"records": 0, "max_records": 2_000, "dropped_records": 0},
            "recent_records": [],
        }

    @staticmethod
    def empty_snapshot() -> dict[str, Any]:
        return {
            "active_mode": "idle",
            "session_state": "idle",
            "target": None,
            "http": {},
            "sse": {"state": "not_connected", "events": 0},
            "recorder": {"records": 0, "max_records": 2_000, "dropped_records": 0},
            "limits": {
                "device_connection_limit": 2,
                "active_device_connections": 0,
                "max_response_bytes": 1_048_576,
                "max_sse_event_bytes": 262_144,
                "max_session_records": 2_000,
                "local_request_concurrency": 1,
                "sse_connect_timeout_seconds": 5.0,
                "sse_inactivity_timeout": "disabled",
            },
            "recent_records": [],
            "churn": SessionManager.empty_churn_snapshot(),
        }


class DragonSniffHandler(BaseHTTPRequestHandler):
    server_version = "DragonSniff/0.1"

    @property
    def manager(self) -> SessionManager:
        return self.server.session_manager  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        print(f"local {self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        if not self._validate_host():
            return
        path = urlsplit(self.path).path
        if path in STATIC_FILES:
            name, content_type = STATIC_FILES[path]
            body = files("dragonsniff.web").joinpath(name).read_bytes()
            self._send(200, body, content_type)
        elif path == "/local/v1/session":
            self._send_json(200, self.manager.snapshot())
        elif path == "/local/v1/session/export":
            body = self.manager.export_jsonl().encode("utf-8")
            self._send(
                200,
                body,
                "application/x-ndjson; charset=utf-8",
                {"Content-Disposition": 'attachment; filename="dragonsniff-session.jsonl"'},
            )
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if not self._validate_host():
            return
        if path not in LOCAL_POST_PATHS:
            self._send_json(404, {"error": "not_found"})
            return
        if not self._validate_origin():
            return
        if not self._validate_json_content_type():
            return
        try:
            body = self._read_json_body()
            if path == "/local/v1/session/start":
                target = body.get("target")
                if not isinstance(target, str):
                    raise ValueError("target must be a string")
                result = self.manager.start(target)
                self._send_json(202, result)
            elif path == "/local/v1/session/stop":
                completed, result = self.manager.stop()
                self._send_json(200 if completed else 202, result)
            elif path == "/local/v1/session/refresh":
                started, result = self.manager.refresh()
                self._send_json(202 if started else 409, result)
            elif path == "/local/v1/session/reconnect-events":
                started, result = self.manager.reconnect()
                self._send_json(202 if started else 409, result)
            elif path == "/local/v1/session/stop-events":
                stopped, result = self.manager.stop_events()
                self._send_json(200 if stopped else 409, result)
            elif path == "/local/v1/churn/start":
                target = body.get("target")
                if not isinstance(target, str):
                    raise ValueError("target must be a string")
                result = self.manager.start_churn(target, body.get("configuration", {}))
                self._send_json(202, result)
            elif path == "/local/v1/churn/stop":
                completed, result = self.manager.stop_churn()
                self._send_json(200 if completed else 202, result)
            else:
                self._send_json(404, {"error": "not_found"})
        except (TargetValidationError, ValueError, RuntimeError) as exc:
            self._send_json(400, {"error": "invalid_request", "message": str(exc)})

    def _allowed_authorities(self) -> set[str]:
        port = self.server.server_port
        authorities = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            authorities.update({"127.0.0.1", "localhost"})
        return authorities

    def _validate_host(self) -> bool:
        authority = self.headers.get("Host", "").strip().lower()
        if authority not in self._allowed_authorities():
            self._send_json(403, {"error": "forbidden", "message": "unexpected Host"})
            return False
        return True

    def _validate_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        authority = self.headers.get("Host", "").strip().lower()
        if origin.strip().lower() != f"http://{authority}":
            self._send_json(403, {"error": "forbidden", "message": "unexpected Origin"})
            return False
        return True

    def _validate_json_content_type(self) -> bool:
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            self._send_json(
                415,
                {"error": "unsupported_media_type", "message": "Content-Type must be application/json"},
            )
            return False
        return True

    def _read_json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_LOCAL_REQUEST_BYTES:
            raise ValueError("request body is too large")
        if length == 0:
            return {}
        try:
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _send_json(self, status: int, value: Any) -> None:
        self._send(
            status,
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'")
        if headers:
            for name, value in headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


class DragonSniffServer(HTTPServer):
    """A deliberately single-request local server with device work off-thread."""

    def __init__(self, address: tuple[str, int], manager: SessionManager | None = None) -> None:
        if address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("DragonSniff V1 only binds to a loopback address")
        self.session_manager = manager or SessionManager()
        super().__init__(address, DragonSniffHandler)

    def server_close(self) -> None:
        observer = self.session_manager.current()
        churn = self.session_manager.current_churn()
        if observer is not None:
            observer.stop(timeout=6.0)
        if churn is not None:
            churn.stop(timeout=6.0)
        super().server_close()
