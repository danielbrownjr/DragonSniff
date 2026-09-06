"""Loopback-only DragonSniff application server."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import logging
import os
from pathlib import Path
from threading import BoundedSemaphore, Event, Lock, Thread, current_thread
from typing import Any, Iterable
from urllib.parse import urlsplit

from .capture import CaptureConfig, CaptureRunner
from .churn import ChurnConfig, ChurnRunner
from .observer import Observer
from .recording import SessionRecorder
from .storage import PersistentSessionRecorder, SessionStore
from .target import DeviceTarget, TargetValidationError, parse_target


LOGGER = logging.getLogger(__name__)


MAX_LOCAL_REQUEST_BYTES = 16_384
LOCAL_POST_PATHS = {
    "/local/v1/session/start",
    "/local/v1/session/stop",
    "/local/v1/session/refresh",
    "/local/v1/session/reconnect-events",
    "/local/v1/session/stop-events",
    "/local/v1/churn/start",
    "/local/v1/churn/stop",
    "/local/v1/capture/start",
    "/local/v1/capture/stop",
}
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/lab": ("index.html", "text/html; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/favicon.ico": ("favicon-32.png", "image/png"),
    "/payload.js": ("payload.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
EXPORT_FILENAMES = {
    "session": "dragonsniff-session.jsonl",
    "capture": "dragonsniff-thermal-capture.jsonl",
    "churn": "dragonsniff-sse-churn.jsonl",
}


class SessionManager:
    def __init__(
        self,
        *,
        store: SessionStore | None = None,
        allowed_targets: Iterable[str] = (),
    ) -> None:
        self._observer: Observer | None = None
        self._churn: ChurnRunner | None = None
        self._capture: CaptureRunner | None = None
        self._lock = Lock()
        self._transition_lock = Lock()
        self._shutdown = Event()
        self._automation_generation = 0
        self._active_automation: str | None = None
        self._resume_target: DeviceTarget | None = None
        self._resume_threads: set[Thread] = set()
        self._last_automation_return: str | None = None
        self._resume_error: str | None = None
        self._store = store
        self._allowed_targets = frozenset(
            parse_target(value).base_url for value in allowed_targets
        )

    def start(self, target_value: str) -> dict[str, Any]:
        with self._transition_lock:
            return self._start_observation(target_value)

    def _start_observation(self, target_value: str) -> dict[str, Any]:
        self._raise_if_shutdown()
        target = parse_target(target_value)
        self._validate_target(target)
        with self._lock:
            previous = self._observer
            churn = self._churn
            capture = self._capture
        if churn is not None and churn.snapshot()["state"] in {
            "running",
            "settling",
            "stopping",
        }:
            raise RuntimeError("a churn run is active; stop it before normal observation")
        if capture is not None and capture.snapshot()["state"] in {"running", "stopping"}:
            raise RuntimeError("a capture run is active; stop it before normal observation")
        if previous is not None and not previous.stop():
            raise RuntimeError("the previous observation session is still stopping")
        observer = self._new_observer(target)
        with self._lock:
            self._raise_if_shutdown()
            try:
                observer.start()
            except Exception as exc:
                self._abort_persistent_start(observer.recorder, exc)
                raise
            self._automation_generation += 1
            self._observer = observer
            self._active_automation = None
            self._resume_target = None
            self._last_automation_return = None
            self._resume_error = None
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
        with self._transition_lock:
            return self._start_churn(target_value, configuration)

    def _start_churn(
        self, target_value: str, configuration: object
    ) -> dict[str, Any]:
        self._raise_if_shutdown()
        target = parse_target(target_value)
        self._validate_target(target)
        config = ChurnConfig.from_value(configuration)
        with self._lock:
            observer = self._observer
            previous = self._churn
            capture = self._capture
        if previous is not None and previous.snapshot()["state"] in {
            "running",
            "settling",
            "stopping",
        }:
            raise RuntimeError("a churn run is already active")
        if capture is not None and capture.snapshot()["state"] in {"running", "stopping"}:
            raise RuntimeError("a capture run is active; stop it before starting churn")
        stopped_target = self._stop_observation_for_automation(observer)
        churn = self._new_churn(target, config)
        with self._lock:
            self._raise_if_shutdown()
            try:
                churn.start()
            except Exception as exc:
                self._abort_persistent_start(churn.recorder, exc)
                raise
            self._automation_generation += 1
            generation = self._automation_generation
            self._observer = None
            self._churn = churn
            self._active_automation = "churn"
            self._resume_target = stopped_target or self._resume_target
            self._last_automation_return = None
            self._resume_error = None
            should_resume = self._resume_target is not None
        if should_resume:
            self._watch_and_resume("churn", churn, generation)
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

    def start_capture(
        self, target_value: str, configuration: object
    ) -> dict[str, Any]:
        with self._transition_lock:
            return self._start_capture(target_value, configuration)

    def _start_capture(
        self, target_value: str, configuration: object
    ) -> dict[str, Any]:
        self._raise_if_shutdown()
        target = parse_target(target_value)
        self._validate_target(target)
        config = CaptureConfig.from_value(configuration)
        with self._lock:
            observer = self._observer
            churn = self._churn
            previous = self._capture
        if churn is not None and churn.snapshot()["state"] in {
            "running",
            "settling",
            "stopping",
        }:
            raise RuntimeError("a churn run is active; stop it before starting capture")
        if previous is not None and previous.snapshot()["state"] in {"running", "stopping"}:
            raise RuntimeError("a capture run is already active")
        stopped_target = self._stop_observation_for_automation(observer)
        capture = self._new_capture(target, config)
        with self._lock:
            self._raise_if_shutdown()
            try:
                capture.start()
            except Exception as exc:
                self._abort_persistent_start(capture.recorder, exc)
                raise
            self._automation_generation += 1
            generation = self._automation_generation
            self._observer = None
            self._capture = capture
            self._active_automation = "capture"
            self._resume_target = stopped_target or self._resume_target
            self._last_automation_return = None
            self._resume_error = None
            should_resume = self._resume_target is not None
        if should_resume:
            self._watch_and_resume("capture", capture, generation)
        return self.snapshot()

    def stop_capture(self) -> tuple[bool, dict[str, Any]]:
        capture = self.current_capture()
        if capture is None:
            raise RuntimeError("no capture run is active")
        completed = capture.stop()
        return completed, self.snapshot()

    def current_capture(self) -> CaptureRunner | None:
        with self._lock:
            return self._capture

    @staticmethod
    def _stop_observation_for_automation(
        observer: Observer | None,
    ) -> DeviceTarget | None:
        if observer is None:
            return None
        state = observer.snapshot()["session_state"]
        if state in {"idle", "stopped"}:
            return None
        if not observer.stop(timeout=2.0):
            raise RuntimeError("the observation session is still stopping")
        return observer.target

    def _watch_and_resume(
        self,
        kind: str,
        runner: ChurnRunner | CaptureRunner,
        generation: int,
    ) -> None:
        thread = Thread(
            target=self._resume_observation_after,
            args=(kind, runner, generation),
            name=f"dragonsniff-resume-{kind}",
            daemon=True,
        )
        with self._lock:
            self._resume_threads.add(thread)
            thread.start()

    def _raise_if_shutdown(self) -> None:
        if self._shutdown.is_set():
            raise RuntimeError("the DragonSniff server is shutting down")

    def _resume_observation_after(
        self,
        kind: str,
        runner: ChurnRunner | CaptureRunner,
        generation: int,
    ) -> None:
        try:
            while not self._shutdown.is_set() and not runner.wait_finished(0.25):
                pass
            with self._transition_lock:
                with self._lock:
                    current = self._churn if kind == "churn" else self._capture
                    target = self._resume_target
                    if (
                        self._shutdown.is_set()
                        or self._automation_generation != generation
                        or current is not runner
                        or self._observer is not None
                        or target is None
                    ):
                        return
                    observer = self._new_observer(target)
                    try:
                        # Starting under the manager lock makes observer installation and
                        # worker launch atomic with respect to shutdown().
                        observer.start()
                    except Exception as exc:
                        self._abort_persistent_start(observer.recorder, exc)
                        self._resume_error = f"{type(exc).__name__}: {exc}"
                        LOGGER.exception("could not resume observation after %s", kind)
                        return
                    self._observer = observer
                    self._resume_target = None
                    self._active_automation = None
                    self._last_automation_return = kind
                    self._resume_error = None
        finally:
            with self._lock:
                self._resume_threads.discard(current_thread())

    def shutdown(self) -> None:
        self._shutdown.set()
        with self._transition_lock:
            with self._lock:
                self._automation_generation += 1
                self._active_automation = None
                self._resume_target = None
                observer = self._observer
                churn = self._churn
                capture = self._capture
                resume_threads = tuple(self._resume_threads)
            if observer is not None:
                observer.stop(timeout=6.0)
            if churn is not None:
                churn.stop(timeout=6.0)
            if capture is not None:
                capture.stop(timeout=6.0)
        for thread in resume_threads:
            if thread is not current_thread():
                thread.join(timeout=6.0)

    def snapshot(self) -> dict[str, Any]:
        observer, churn, capture, active_automation, _, automation_return = (
            self._authoritative_context()
        )
        if observer is not None:
            result = observer.snapshot()
            result["active_mode"] = "observation"
            result["automation_return"] = automation_return
            result["churn"] = (
                churn.snapshot() if churn is not None else self.empty_churn_snapshot()
            )
            result["capture"] = (
                capture.snapshot() if capture is not None else self.empty_capture_snapshot()
            )
            return result
        result = self.empty_snapshot()
        result["automation_return"] = automation_return
        result["churn"] = (
            churn.snapshot() if churn is not None else self.empty_churn_snapshot()
        )
        result["capture"] = (
            capture.snapshot() if capture is not None else self.empty_capture_snapshot()
        )
        if active_automation == "churn" and churn is not None:
            result["active_mode"] = "churn"
            result["target"] = result["churn"]["target"]
            result["recorder"] = result["churn"]["recorder"]
            result["recent_records"] = result["churn"]["recent_records"]
            result["limits"]["active_device_connections"] = result["churn"][
                "active_device_connections"
            ]
            result["limits"]["device_connection_limit"] = result["churn"][
                "device_connection_limit"
            ]
        elif active_automation == "capture" and capture is not None:
            result["active_mode"] = "capture"
            result["target"] = result["capture"]["target"]
            result["recorder"] = result["capture"]["recorder"]
            result["recent_records"] = result["capture"]["recent_records"]
            result["limits"]["active_device_connections"] = result["capture"][
                "active_device_connections"
            ]
            result["limits"]["device_connection_limit"] = result["capture"][
                "device_connection_limit"
            ]
        return result

    def export_jsonl(self) -> str:
        _, _, _, _, recorder, _ = self._authoritative_context()
        return recorder.export_jsonl() if recorder is not None else ""

    def export_churn_jsonl(self) -> str | None:
        churn = self.current_churn()
        return churn.recorder.export_jsonl() if churn is not None else None

    def export_capture_jsonl(self) -> str | None:
        capture = self.current_capture()
        return capture.recorder.export_jsonl() if capture is not None else None

    def history(self) -> dict[str, Any]:
        return {
            "persistent": self._store is not None,
            "sessions": self._store.list_sessions() if self._store is not None else [],
        }

    def historical_session(self, session_id: str) -> dict[str, Any] | None:
        return self._store.get_session(session_id) if self._store is not None else None

    def historical_evidence_path(self, session_id: str) -> Path | None:
        return self._store.evidence_path(session_id) if self._store is not None else None

    def _validate_target(self, target: DeviceTarget) -> None:
        if self._allowed_targets and target.base_url not in self._allowed_targets:
            raise TargetValidationError("target is not in the configured allowlist")

    def _new_observer(self, target: DeviceTarget) -> Observer:
        if self._store is None:
            return Observer(target)
        return Observer(
            target,
            recorder=self._store.create_recorder(
                "observation", target.base_url, 2_000
            ),
        )

    def _new_churn(self, target: DeviceTarget, config: ChurnConfig) -> ChurnRunner:
        if self._store is None:
            return ChurnRunner(target, config)
        return ChurnRunner(
            target,
            config,
            recorder=self._store.create_recorder("churn", target.base_url, 2_000),
        )

    def _new_capture(
        self, target: DeviceTarget, config: CaptureConfig
    ) -> CaptureRunner:
        if self._store is None:
            return CaptureRunner(target, config)
        return CaptureRunner(
            target,
            config,
            recorder=self._store.create_recorder(
                "capture", target.base_url, config.estimated_records()
            ),
        )

    @staticmethod
    def _abort_persistent_start(recorder: SessionRecorder, exc: Exception) -> None:
        if isinstance(recorder, PersistentSessionRecorder):
            try:
                recorder.abort_start(f"{type(exc).__name__}: {exc}")
            except Exception:
                LOGGER.exception("could not mark failed persistent session start")

    def _authoritative_context(
        self,
    ) -> tuple[
        Observer | None,
        ChurnRunner | None,
        CaptureRunner | None,
        str | None,
        SessionRecorder | None,
        dict[str, Any],
    ]:
        """Resolve one active surface for both snapshot and JSONL export."""
        with self._lock:
            observer = self._observer
            churn = self._churn
            capture = self._capture
            active_automation = self._active_automation
            recorder = (
                observer.recorder
                if observer is not None
                else churn.recorder
                if active_automation == "churn" and churn is not None
                else capture.recorder
                if active_automation == "capture" and capture is not None
                else None
            )
            return (
                observer,
                churn,
                capture,
                active_automation,
                recorder,
                self._automation_return_snapshot_locked(),
            )

    def _automation_return_snapshot_locked(self) -> dict[str, Any]:
        return {
            "pending": self._resume_target is not None,
            "resumed_after": self._last_automation_return,
            "error": self._resume_error,
        }

    @staticmethod
    def empty_capture_snapshot() -> dict[str, Any]:
        config = CaptureConfig()
        return {
            "state": "idle",
            "run_id": None,
            "target": None,
            "configuration": config.snapshot(),
            "profile": config.profile_name(),
            "profiles": config.profile_snapshots(),
            "bounds": config.bounds(),
            "estimated_records": config.estimated_records(),
            "samples_completed": 0,
            "fetches_completed": 0,
            "state_successes": 0,
            "state_failures": 0,
            "health_successes": 0,
            "health_failures": 0,
            "latest_info": None,
            "latest_state": None,
            "latest_health": None,
            "initial_boot_id": None,
            "latest_boot_id": None,
            "boot_id_changed": False,
            "boot_id_changes": [],
            "cleanup_complete": True,
            "failure": None,
            "start_timestamp": None,
            "end_timestamp": None,
            "elapsed_ms": 0.0,
            "active_device_connections": 0,
            "device_connection_limit": 1,
            "recorder": {
                "records": 0,
                "max_records": config.estimated_records(),
                "dropped_records": 0,
            },
            "recent_records": [],
        }

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
            "settlement": {
                "state": "not_started",
                "baseline_sse_clients": None,
                "latest_sse_clients": None,
                "max_wait_seconds": ChurnRunner.SETTLEMENT_SAMPLE_SECONDS[-1],
                "elapsed_ms": 0.0,
                "samples": [],
            },
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
                "local_request_concurrency": 8,
                "sse_connect_timeout_seconds": 5.0,
                "sse_inactivity_timeout": "disabled",
            },
            "recent_records": [],
            "churn": SessionManager.empty_churn_snapshot(),
            "capture": SessionManager.empty_capture_snapshot(),
            "automation_return": {
                "pending": False,
                "resumed_after": None,
                "error": None,
            },
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
        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
        elif path in STATIC_FILES:
            name, content_type = STATIC_FILES[path]
            body = files("dragonsniff.web").joinpath(name).read_bytes()
            self._send(200, body, content_type)
        elif path == "/local/v1/session":
            self._send_json(200, self.manager.snapshot())
        elif path == "/local/v1/history":
            self._send_json(200, self.manager.history())
        elif self._history_route(path, "") is not None:
            session_id = self._history_route(path, "")
            session = self.manager.historical_session(session_id)
            if session is None:
                self._send_json(404, {"error": "session_not_found"})
                return
            self._send_json(200, session)
        elif self._history_route(path, "/export") is not None:
            session_id = self._history_route(path, "/export")
            session = self.manager.historical_session(session_id)
            evidence_path = self.manager.historical_evidence_path(session_id)
            if session is None or evidence_path is None:
                self._send_json(404, {"error": "session_not_found"})
                return
            created = str(session["created_at"]).replace(":", "").replace("+", "-")
            filename = (
                f'dragonsniff-{session["kind"]}-{created}-{session_id[:8]}.jsonl'
            )
            self._send_file(evidence_path, filename)
        elif path == "/local/v1/session/export":
            body = self.manager.export_jsonl().encode("utf-8")
            self._send(
                200,
                body,
                "application/x-ndjson; charset=utf-8",
                {
                    "Content-Disposition": (
                        f'attachment; filename="{EXPORT_FILENAMES["session"]}"'
                    )
                },
            )
        elif path == "/local/v1/churn/export":
            export = self.manager.export_churn_jsonl()
            if export is None:
                self._send_json(404, {"error": "churn_evidence_not_available"})
                return
            body = export.encode("utf-8")
            self._send(
                200,
                body,
                "application/x-ndjson; charset=utf-8",
                {
                    "Content-Disposition": (
                        f'attachment; filename="{EXPORT_FILENAMES["churn"]}"'
                    )
                },
            )
        elif path == "/local/v1/capture/export":
            export = self.manager.export_capture_jsonl()
            if export is None:
                self._send_json(404, {"error": "capture_evidence_not_available"})
                return
            body = export.encode("utf-8")
            self._send(
                200,
                body,
                "application/x-ndjson; charset=utf-8",
                {
                    "Content-Disposition": (
                        f'attachment; filename="{EXPORT_FILENAMES["capture"]}"'
                    )
                },
            )
        else:
            self._send_json(404, {"error": "not_found"})

    @staticmethod
    def _history_route(path: str, suffix: str) -> str | None:
        prefix = "/local/v1/history/"
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        session_id = path[len(prefix) : len(path) - len(suffix) if suffix else None]
        return session_id if len(session_id) == 32 and session_id.isalnum() else None

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
            elif path == "/local/v1/capture/start":
                target = body.get("target")
                if not isinstance(target, str):
                    raise ValueError("target must be a string")
                result = self.manager.start_capture(
                    target, body.get("configuration", {})
                )
                self._send_json(202, result)
            elif path == "/local/v1/capture/stop":
                completed, result = self.manager.stop_capture()
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

    def _send_file(self, path: Path, filename: str) -> None:
        try:
            stream = path.open("rb")
        except FileNotFoundError:
            self._send_json(404, {"error": "session_not_found"})
            return
        with stream:
            remaining = os.fstat(stream.fileno()).st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Content-Length", str(remaining))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            while remaining and (chunk := stream.read(min(64 * 1024, remaining))):
                self.wfile.write(chunk)
                remaining -= len(chunk)

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


class DragonSniffServer(ThreadingHTTPServer):
    """A bounded threaded server with a loopback-oriented browser boundary."""

    REQUEST_WORKER_LIMIT = 8
    REQUEST_SLOT_TIMEOUT = 0.25
    REQUEST_SOCKET_TIMEOUT = 5.0

    def __init__(
        self,
        address: tuple[str, int],
        manager: SessionManager | None = None,
        *,
        allow_wildcard_bind: bool = False,
    ) -> None:
        loopback = {"127.0.0.1", "localhost", "::1"}
        if address[0] not in loopback and not (
            allow_wildcard_bind and address[0] == "0.0.0.0"
        ):
            raise ValueError(
                "DragonSniff binds to loopback unless 0.0.0.0 is explicitly enabled"
            )
        self.session_manager = manager or SessionManager()
        self._request_slots = BoundedSemaphore(self.REQUEST_WORKER_LIMIT)
        super().__init__(address, DragonSniffHandler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._request_slots.acquire(timeout=self.REQUEST_SLOT_TIMEOUT):
            body = b'{"error":"server_busy"}'
            response = (
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
                + body
            )
            try:
                request.sendall(response)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            request.settimeout(self.REQUEST_SOCKET_TIMEOUT)
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    def server_close(self) -> None:
        self.session_manager.shutdown()
        super().server_close()
