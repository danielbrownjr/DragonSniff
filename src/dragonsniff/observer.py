"""One-device observation session built on the reusable client and recorder."""

from __future__ import annotations

from copy import deepcopy
from threading import Event, Lock, Thread
import time
from typing import Any

from .client import DragonClient, JSON_ENDPOINTS
from .recording import SessionRecorder
from .target import DeviceTarget


class Observer:
    """Own one bounded Dragon observation session and its lifecycle."""

    def __init__(
        self,
        target: DeviceTarget,
        *,
        max_records: int = 2_000,
        connection_limit: int = 2,
        client: DragonClient | None = None,
    ) -> None:
        self.target = target
        self.recorder = client.recorder if client is not None else SessionRecorder(max_records)
        self.client = client or DragonClient(
            target, self.recorder, connection_limit=connection_limit
        )
        self._lock = Lock()
        self._session_stop = Event()
        self._stream_stop: Event | None = None
        self._stream_thread: Thread | None = None
        self._refresh_thread: Thread | None = None
        self._generation = 0
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
        self.recorder.append(
            "session_started",
            target=self.target.base_url,
            limits=self.limits(),
        )
        self.refresh(connect_events=True)

    def refresh(self, *, connect_events: bool = False) -> bool:
        with self._lock:
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
                    "state": "available" if result["ok"] else "unavailable",
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

    def stop_events(self) -> bool:
        with self._lock:
            thread = self._stream_thread
            stream_stop = self._stream_stop
        if thread is None or stream_stop is None or not thread.is_alive():
            return True
        stream_stop.set()
        self.client.close_stream()
        thread.join(timeout=2)
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

    def stop(self) -> None:
        with self._lock:
            if self._state["session_state"] == "stopped":
                return
            self._state["session_state"] = "stopping"
        self._session_stop.set()
        self.stop_events()
        if self._refresh_thread is not None and self._refresh_thread.is_alive():
            self._refresh_thread.join(timeout=2)
        with self._lock:
            self._state["session_state"] = "stopped"
            self._state["sse"]["state"] = "closed"
        self.recorder.append("session_stopped", target=self.target.base_url)

    def snapshot(self, recent_records: int = 100) -> dict[str, Any]:
        with self._lock:
            state = deepcopy(self._state)
        records = self.recorder.snapshot()
        state.update(
            {
                "recorder": self.recorder.summary(),
                "limits": self.limits(),
                "recent_records": records[-recent_records:],
                "server_monotonic_ns": time.monotonic_ns(),
            }
        )
        return state

    def limits(self) -> dict[str, int]:
        return {
            "device_connection_limit": self.client.budget.limit,
            "active_device_connections": self.client.budget.active,
            "max_response_bytes": self.client.max_response_bytes,
            "max_sse_event_bytes": self.client.max_event_bytes,
            "max_session_records": self.recorder.max_records,
            "local_request_concurrency": 1,
            "sse_connect_timeout_seconds": self.client.sse_connect_timeout,
            "sse_inactivity_timeout": "disabled",
        }
