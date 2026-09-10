"""One-device observation session built on the reusable client and recorder."""

from __future__ import annotations

from copy import deepcopy
from threading import Event, Lock, Thread
import time
from typing import Any

from .client import DragonClient, JSON_ENDPOINTS
from .prusalink import PrusaLinkConfig, PrusaLinkSource
from .recording import SessionRecorder
from .target import DeviceTarget


BASE_LIVE_RECORDS = 2_000


class Observer:
    """Own one bounded Dragon observation session and its lifecycle."""

    def __init__(
        self,
        target: DeviceTarget,
        *,
        max_records: int = BASE_LIVE_RECORDS,
        connection_limit: int = 2,
        recorder: SessionRecorder | None = None,
        client: DragonClient | None = None,
        prusalink_config: PrusaLinkConfig | None = None,
        prusalink_source: PrusaLinkSource | None = None,
    ) -> None:
        source_config = (
            prusalink_config
            or (prusalink_source.config if prusalink_source is not None else None)
            or PrusaLinkConfig()
        )
        source_reserved_records = source_config.live_observation_reserved_records()
        required_records = max_records + source_reserved_records
        self.target = target
        self.recorder = (
            client.recorder
            if client is not None
            else (recorder or SessionRecorder(required_records))
        )
        if source_config.enabled and self.recorder.max_records < required_records:
            raise ValueError(
                "PrusaLink live recorder is smaller than the Dragon baseline "
                "plus source reserve"
            )
        self.client = client or DragonClient(
            target, self.recorder, connection_limit=connection_limit
        )
        self._base_live_records = max_records
        self._source_reserved_records = source_reserved_records
        self.prusalink = prusalink_source or PrusaLinkSource(
            source_config, self.recorder
        )
        if self.prusalink.recorder is not self.recorder:
            raise ValueError("PrusaLink source must share the observation recorder")
        self._lock = Lock()
        self._session_stop = Event()
        self._stream_stop: Event | None = None
        self._stream_thread: Thread | None = None
        self._refresh_thread: Thread | None = None
        self._generation = 0
        self._stop_recorded = False
        self._state: dict[str, Any] = {
            "session_state": "new",
            "target": target.base_url,
            "http": {path: {"state": "not_requested"} for path in JSON_ENDPOINTS},
            "sse": {"state": "not_connected", "events": 0, "last_event": None},
        }

    def start(self) -> None:
        with self._lock:
            if self._state["session_state"] not in {"new", "stopped"}:
                return
            self._state["session_state"] = "starting"
            self._session_stop.clear()
            self._stop_recorded = False
        self.recorder.append(
            "session_started",
            target=self.target.base_url,
            limits=self.limits(),
        )
        self.prusalink.start(context={"owner": "observation"})
        self.refresh(connect_events=True)

    def refresh(self, *, connect_events: bool = False) -> bool:
        with self._lock:
            if self._state["session_state"] in {"stopping", "stopped"}:
                return False
            if self._refresh_thread is not None and self._refresh_thread.is_alive():
                return False
            thread = Thread(
                target=self._refresh_worker,
                args=(connect_events,),
                name="dragonsniff-http",
                daemon=True,
            )
            self._refresh_thread = thread
        thread.start()
        return True

    def _refresh_worker(self, connect_events: bool) -> None:
        for path in JSON_ENDPOINTS:
            if self._session_stop.is_set():
                return
            with self._lock:
                self._state["http"][path] = {"state": "requesting"}
            result = self.client.fetch_json(path)
            with self._lock:
                self._state["http"][path] = {
                    "state": "available" if result["http_ok"] else "unavailable",
                    **result,
                }
        with self._lock:
            if self._state["session_state"] not in {"stopping", "stopped"}:
                self._state["session_state"] = "observing"
        if connect_events and not self._session_stop.is_set():
            self.reconnect_events()

    def reconnect_events(self) -> bool:
        if not self.stop_events():
            return False
        with self._lock:
            if self._state["session_state"] in {"stopping", "stopped"}:
                return False
            self._generation += 1
            generation = self._generation
            stream_stop = Event()
            self._stream_stop = stream_stop
            self._state["sse"] = {
                "state": "connecting",
                "generation": generation,
                "events": 0,
                "last_event": None,
            }
        thread = Thread(
            target=self.client.stream_events,
            args=(
                stream_stop,
                lambda event: self._on_event(generation, event),
                lambda state, details: self._on_sse_state(generation, state, details),
            ),
            name=f"dragonsniff-sse-{generation}",
            daemon=True,
        )
        with self._lock:
            self._stream_thread = thread
        thread.start()
        return True

    def _on_event(self, generation: int, event: dict[str, Any]) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._state["sse"]["events"] += 1
            self._state["sse"]["last_event"] = deepcopy(event)

    def _on_sse_state(
        self, generation: int, state: str, details: dict[str, Any]
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
            closing = state == "closed"
            if closing and details.get("reason") in {"unavailable", "error", "stopped"}:
                state = details["reason"]
            self._state["sse"]["state"] = state
            self._state["sse"]["close_details" if closing else "details"] = deepcopy(details)

    def stop_events(self, timeout: float = 2.0) -> bool:
        with self._lock:
            thread = self._stream_thread
            stream_stop = self._stream_stop
        if thread is None or stream_stop is None or not thread.is_alive():
            with self._lock:
                if self._stream_thread is thread:
                    self._stream_thread = None
                    self._stream_stop = None
            return True
        stream_stop.set()
        self.client.close_stream()
        thread.join(timeout=max(0.0, timeout))
        if thread.is_alive():
            details = {"reason": "stop_timeout", "generation": self._generation}
            self.recorder.append("sse_stop_timeout", **details)
            self._on_sse_state(self._generation, "error", details)
            return False
        with self._lock:
            if self._stream_thread is thread:
                self._stream_thread = None
                self._stream_stop = None
        return True

    def stop(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            if self._state["session_state"] == "stopped":
                return True
            self._state["session_state"] = "stopping"
        self._session_stop.set()
        self.prusalink.request_stop()
        self.stop_events(timeout=max(0.0, deadline - time.monotonic()))
        if self._refresh_thread is not None and self._refresh_thread.is_alive():
            self._refresh_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        self.prusalink.stop(timeout=max(0.0, deadline - time.monotonic()))
        return self._finish_stop_if_complete()

    def _finish_stop_if_complete(self) -> bool:
        with self._lock:
            if self._state["session_state"] == "stopped":
                return True
            if self._state["session_state"] != "stopping":
                return False
            workers = (self._stream_thread, self._refresh_thread)
            if any(thread is not None and thread.is_alive() for thread in workers):
                return False
            if self.prusalink.is_alive:
                return False
            if self.client.budget.active != 0:
                return False
            self._stream_thread = None
            self._stream_stop = None
            self._refresh_thread = None
            self._state["session_state"] = "stopped"
            self._state["sse"]["state"] = "closed"
            record_stop = not self._stop_recorded
            self._stop_recorded = True
        if record_stop:
            self.recorder.append("session_stopped", target=self.target.base_url)
        return True

    def snapshot(self, recent_records: int = 100) -> dict[str, Any]:
        self._finish_stop_if_complete()
        with self._lock:
            state = deepcopy(self._state)
        records = self.recorder.snapshot()
        state.update(
            {
                "recorder": self.recorder.summary(),
                "limits": self.limits(),
                "recent_records": records[-recent_records:],
                "server_monotonic_ns": time.monotonic_ns(),
                "prusalink": self.prusalink.snapshot(),
            }
        )
        return state

    def limits(self) -> dict[str, Any]:
        return {
            "device_connection_limit": self.client.budget.limit,
            "active_device_connections": self.client.budget.active,
            "max_response_bytes": self.client.max_response_bytes,
            "max_sse_event_bytes": self.client.max_event_bytes,
            "max_session_records": self.recorder.max_records,
            "base_live_records": self._base_live_records,
            "source_reserved_records": self._source_reserved_records,
            "local_request_concurrency": 8,
            "sse_connect_timeout_seconds": self.client.sse_connect_timeout,
            "sse_inactivity_timeout": "disabled",
        }
