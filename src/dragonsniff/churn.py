"""Bounded sequential SSE lifecycle exercise for one Dragon target."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import Event, Lock, Thread, current_thread
import time
from typing import Any
from uuid import uuid4

from .client import DragonClient
from .recording import SessionRecorder
from .target import DeviceTarget


@dataclass(frozen=True, slots=True)
class ChurnConfig:
    cycles: int = 3
    observe_seconds: float = 2.0
    max_events: int = 3
    delay_seconds: float = 0.5

    MIN_CYCLES = 1
    MAX_CYCLES = 20
    MIN_OBSERVE_SECONDS = 0.25
    MAX_OBSERVE_SECONDS = 15.0
    MIN_EVENTS = 1
    MAX_EVENTS = 25
    MIN_DELAY_SECONDS = 0.1
    MAX_DELAY_SECONDS = 5.0

    @classmethod
    def profiles(cls) -> dict[str, "ChurnConfig"]:
        """Return the reusable, bounded comparison profiles shown by the UI."""
        return {
            "Baseline": cls(3, 2.0, 3, 0.5),
            "Extended": cls(10, 5.0, 5, 0.25),
            "Stress": cls(20, 10.0, 10, 0.1),
        }

    @classmethod
    def profile_snapshots(cls) -> dict[str, dict[str, int | float]]:
        profiles = cls.profiles()
        for config in profiles.values():
            config.validate()
        return {name: config.snapshot() for name, config in profiles.items()}

    def profile_name(self) -> str:
        for name, config in self.profiles().items():
            if self == config:
                return name
        return "Custom"

    @classmethod
    def from_value(cls, value: object) -> "ChurnConfig":
        if not isinstance(value, dict):
            raise ValueError("configuration must be a JSON object")
        allowed = {"cycles", "observe_seconds", "max_events", "delay_seconds"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown configuration field: {sorted(unknown)[0]}")

        defaults = cls()
        cycles = value.get("cycles", defaults.cycles)
        max_events = value.get("max_events", defaults.max_events)
        observe_seconds = value.get("observe_seconds", defaults.observe_seconds)
        delay_seconds = value.get("delay_seconds", defaults.delay_seconds)
        if isinstance(cycles, bool) or not isinstance(cycles, int):
            raise ValueError("cycles must be an integer")
        if isinstance(max_events, bool) or not isinstance(max_events, int):
            raise ValueError("max_events must be an integer")
        if isinstance(observe_seconds, bool) or not isinstance(observe_seconds, (int, float)):
            raise ValueError("observe_seconds must be a number")
        if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, (int, float)):
            raise ValueError("delay_seconds must be a number")

        result = cls(cycles, float(observe_seconds), max_events, float(delay_seconds))
        result.validate()
        return result

    def validate(self) -> None:
        bounds = (
            (
                self.MIN_CYCLES <= self.cycles <= self.MAX_CYCLES,
                "cycles",
                self.MIN_CYCLES,
                self.MAX_CYCLES,
            ),
            (
                self.MIN_OBSERVE_SECONDS <= self.observe_seconds <= self.MAX_OBSERVE_SECONDS,
                "observe_seconds",
                self.MIN_OBSERVE_SECONDS,
                self.MAX_OBSERVE_SECONDS,
            ),
            (
                self.MIN_EVENTS <= self.max_events <= self.MAX_EVENTS,
                "max_events",
                self.MIN_EVENTS,
                self.MAX_EVENTS,
            ),
            (
                self.MIN_DELAY_SECONDS <= self.delay_seconds <= self.MAX_DELAY_SECONDS,
                "delay_seconds",
                self.MIN_DELAY_SECONDS,
                self.MAX_DELAY_SECONDS,
            ),
        )
        for accepted, name, lower, upper in bounds:
            if not accepted:
                raise ValueError(f"{name} must be between {lower} and {upper}")

    def snapshot(self) -> dict[str, int | float]:
        return asdict(self)

    @classmethod
    def bounds(cls) -> dict[str, dict[str, int | float]]:
        return {
            "cycles": {"min": cls.MIN_CYCLES, "max": cls.MAX_CYCLES},
            "observe_seconds": {
                "min": cls.MIN_OBSERVE_SECONDS,
                "max": cls.MAX_OBSERVE_SECONDS,
            },
            "max_events": {"min": cls.MIN_EVENTS, "max": cls.MAX_EVENTS},
            "delay_seconds": {
                "min": cls.MIN_DELAY_SECONDS,
                "max": cls.MAX_DELAY_SECONDS,
            },
        }


class ChurnRunner:
    """Run one sequential, explicitly bounded SSE churn exercise."""

    TERMINAL_STATES = {"completed", "cancelled", "failed"}

    def __init__(
        self,
        target: DeviceTarget,
        config: ChurnConfig,
        *,
        recorder: SessionRecorder | None = None,
        client: DragonClient | None = None,
    ) -> None:
        config.validate()
        self.target = target
        self.config = config
        self.recorder = client.recorder if client is not None else (recorder or SessionRecorder(2_000))
        self.client = client or DragonClient(target, self.recorder, connection_limit=2)
        self.run_id = uuid4().hex
        self._lock = Lock()
        self._cancel = Event()
        self._thread: Thread | None = None
        self._stream_thread: Thread | None = None
        self._stream_stop: Event | None = None
        self._pending_terminal: str | None = None
        self._terminal_recorded = False
        self._started_ns: int | None = None
        self._state: dict[str, Any] = {
            "state": "idle",
            "run_id": self.run_id,
            "target": target.base_url,
            "configuration": config.snapshot(),
            "profile": config.profile_name(),
            "profiles": config.profile_snapshots(),
            "bounds": config.bounds(),
            "current_cycle": 0,
            "total_cycles": config.cycles,
            "active_churn_connections": 0,
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
        }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._state["state"] != "idle":
                raise RuntimeError("churn run has already started")
            self._state["state"] = "running"
            self._state["cleanup_complete"] = False
            self._started_ns = time.monotonic_ns()
            started = self.recorder.append(
                "churn_run_started",
                run_id=self.run_id,
                target=self.target.base_url,
                configuration=self.config.snapshot(),
                profile=self.config.profile_name(),
                bounds=self.config.bounds(),
            )
            self._state["start_timestamp"] = started["timestamp"]
            thread = Thread(
                target=self._run,
                name=f"dragonsniff-churn-{self.run_id[:8]}",
                daemon=True,
            )
            self._thread = thread
        thread.start()
        return self.snapshot()

    def stop(self, timeout: float = 2.0) -> bool:
        with self._lock:
            if self._state["state"] in self.TERMINAL_STATES:
                return True
            if self._state["state"] == "idle":
                return True
            self._state["state"] = "stopping"
            thread = self._thread
            stream_stop = self._stream_stop
        self._cancel.set()
        if stream_stop is not None:
            stream_stop.set()
        self.client.close_stream()
        if thread is not None and thread is not current_thread():
            thread.join(timeout=max(0.0, timeout))
        return self._finish_if_complete()

    def _run(self) -> None:
        outcome = "completed"
        try:
            self._sample_health("before_run", cycle=0)
            for cycle in range(1, self.config.cycles + 1):
                if self._cancel.is_set():
                    outcome = "cancelled"
                    break
                self._run_cycle(cycle)
                if self._cancel.is_set():
                    outcome = "cancelled"
                    break
                if cycle < self.config.cycles and self._cancel.wait(self.config.delay_seconds):
                    outcome = "cancelled"
                    break
            if not self._cancel.is_set():
                with self._lock:
                    final_cycle = self._state["current_cycle"]
                self._sample_health("after_run", cycle=final_cycle)
        except Exception as exc:
            outcome = "cancelled" if self._cancel.is_set() else "failed"
            details = {"type": type(exc).__name__, "message": str(exc)}
            with self._lock:
                self._state["failure"] = details
            self.recorder.append("churn_internal_failure", run_id=self.run_id, **details)
        finally:
            self._cancel_active_stream()
            with self._lock:
                self._pending_terminal = "cancelled" if self._cancel.is_set() else outcome
                if self._state["state"] not in self.TERMINAL_STATES:
                    self._state["state"] = "stopping"

    def _run_cycle(self, cycle: int) -> None:
        cycle_started_ns = time.monotonic_ns()
        opened_ns: list[int] = []
        summary: dict[str, Any] = {
            "cycle": cycle,
            "outcome": "connecting",
            "events": 0,
            "connection": None,
            "close": None,
            "close_reason": None,
            "elapsed_ms": None,
        }
        with self._lock:
            self._state["current_cycle"] = cycle
            self._state["cycles"].append(summary)
        context = {"run_id": self.run_id, "cycle": cycle, "owner": "churn"}
        self.recorder.append("churn_cycle_started", **context)
        stream_stop = Event()
        stream_done = Event()
        opened = Event()
        event_bound = Event()
        stream_failure: list[dict[str, str]] = []

        def on_event(event: dict[str, Any]) -> None:
            with self._lock:
                summary["events"] += 1
                self._state["events_observed"] += 1
                if event.get("parse_error"):
                    self._state["parse_failures"] += 1
                if summary["events"] >= self.config.max_events:
                    event_bound.set()
                    stream_stop.set()

        def on_state(state: str, details: dict[str, Any]) -> None:
            with self._lock:
                if state == "connecting":
                    summary["connection"] = {"state": state, **deepcopy(details)}
                elif state == "open":
                    summary["connection"] = {"state": state, **deepcopy(details)}
                    opened_ns.append(time.monotonic_ns())
                    opened.set()
                    summary["outcome"] = "connected"
                    self._state["successful_connections"] += 1
                    self._state["active_churn_connections"] = 1
                elif state == "unavailable":
                    summary["connection"] = {"state": state, **deepcopy(details)}
                    summary["outcome"] = (
                        "capacity_rejected" if details.get("status") == 503 else "http_failure"
                    )
                    if details.get("status") == 503:
                        self._state["rejected_connections"] += 1
                    else:
                        self._state["http_failures"] += 1
                    if details.get("parse_error"):
                        self._state["parse_failures"] += 1
                elif state == "error":
                    summary["connection"] = {"state": state, **deepcopy(details)}
                    summary["outcome"] = "transport_failure"
                    if str(details.get("error", "")).startswith(
                        "TimeoutError: device connection budget"
                    ):
                        self._state["local_resource_failures"] += 1
                    else:
                        self._state["transport_failures"] += 1
                elif state == "closed":
                    reason = details.get("reason")
                    summary["close"] = deepcopy(details)
                    summary["close_reason"] = reason
                    self._state["active_churn_connections"] = 0
                    if reason == "end_of_stream":
                        self._state["remote_eof"] += 1

        def stream_worker() -> None:
            try:
                self.client.stream_events(stream_stop, on_event, on_state, context=context)
            except Exception as exc:
                details = {"type": type(exc).__name__, "message": str(exc)}
                stream_failure.append(details)
                self.recorder.append("churn_stream_internal_failure", **context, **details)
            finally:
                stream_done.set()

        thread = Thread(
            target=stream_worker,
            name=f"dragonsniff-churn-sse-{cycle}",
            daemon=True,
        )
        with self._lock:
            self._stream_stop = stream_stop
            self._stream_thread = thread
        thread.start()

        connection_deadline = time.monotonic() + self.client.sse_connect_timeout + 1.0
        while not opened.is_set() and not stream_done.is_set() and not self._cancel.is_set():
            if time.monotonic() >= connection_deadline:
                stream_stop.set()
                self.client.close_stream()
                break
            stream_done.wait(0.02)

        if opened.is_set() and not self._cancel.is_set():
            self._sample_health("after_connection", cycle=cycle)
            observe_deadline = opened_ns[0] + int(self.config.observe_seconds * 1_000_000_000)
            while not stream_done.is_set() and not self._cancel.is_set() and not event_bound.is_set():
                remaining = (observe_deadline - time.monotonic_ns()) / 1_000_000_000
                if remaining <= 0:
                    break
                stream_done.wait(min(0.02, remaining))

        deliberate = opened.is_set() and (
            self._cancel.is_set() or event_bound.is_set() or not stream_done.is_set()
        )
        if deliberate:
            reason = "cancellation" if self._cancel.is_set() else (
                "event_bound" if event_bound.is_set() else "duration_bound"
            )
            self.recorder.append("churn_deliberate_disconnect", **context, reason=reason)
        stream_stop.set()
        self.client.close_stream()
        thread.join(timeout=self.client.sse_connect_timeout + 1.0)
        cleanup_failure: str | None = None
        if thread.is_alive():
            self.recorder.append("churn_cleanup_timeout", **context, worker=thread.name)
            with self._lock:
                self._state["local_resource_failures"] += 1
                summary["outcome"] = "cleanup_timeout"
            cleanup_failure = "churn stream worker did not stop within cleanup bound"
        elif stream_failure:
            with self._lock:
                summary["outcome"] = "controller_failure"
            cleanup_failure = "churn stream worker raised an internal failure"
        if not self._cancel.is_set() and cleanup_failure is None:
            if opened.is_set():
                self._sample_health("after_disconnect", cycle=cycle)
            else:
                self._sample_health("after_attempt", cycle=cycle)

        with self._lock:
            if summary["outcome"] == "connected":
                if self._cancel.is_set():
                    summary["outcome"] = "cancelled"
                elif summary["close_reason"] == "end_of_stream":
                    summary["outcome"] = "remote_eof"
                else:
                    summary["outcome"] = "disconnected"
            summary["cleanup"] = {
                "worker_terminated": not thread.is_alive(),
                "active_device_connections": self.client.budget.active,
            }
            summary["elapsed_ms"] = round(
                (time.monotonic_ns() - cycle_started_ns) / 1_000_000, 3
            )
            cycle_result = deepcopy(summary)
            if not thread.is_alive() and self._stream_thread is thread:
                self._stream_thread = None
                self._stream_stop = None
        self.recorder.append("churn_cycle_finished", **context, summary=cycle_result)
        if cleanup_failure is not None:
            raise RuntimeError(cleanup_failure)

    def _sample_health(self, point: str, *, cycle: int) -> None:
        context = {
            "run_id": self.run_id,
            "cycle": cycle,
            "owner": "churn",
            "sample_point": point,
        }
        result = self.client.fetch_json("/api/v2/health", context=context)
        parsed = result.get("parsed")
        observed = self._health_observations(parsed) if result.get("ok") else {}
        sample = {**result, "sample_point": point, "observed": observed}
        record = self.recorder.append("churn_health_sample", **context, sample=sample)
        boot_id = observed.get("boot_id")
        with self._lock:
            self._state["latest_health"] = deepcopy(sample)
            if result.get("parse_error"):
                self._state["parse_failures"] += 1
            previous = self._state["latest_boot_id"]
            if boot_id is not None and boot_id != "":
                if self._state["initial_boot_id"] is None:
                    self._state["initial_boot_id"] = boot_id
                elif previous is not None and boot_id != previous:
                    change = {
                        "previous": previous,
                        "current": boot_id,
                        "cycle": cycle,
                        "sample_point": point,
                        "timestamp": record["timestamp"],
                    }
                    self._state["boot_id_changed"] = True
                    self._state["boot_id_changes"].append(change)
                    self.recorder.append("churn_boot_id_changed", run_id=self.run_id, **change)
                self._state["latest_boot_id"] = boot_id

    @staticmethod
    def _health_observations(parsed: object) -> dict[str, Any]:
        if not isinstance(parsed, dict):
            return {}
        names = (
            "boot_id",
            "uptime_ms",
            "free_heap",
            "minimum_free_heap",
            "free_heap_bytes",
            "minimum_free_heap_bytes",
            "largest_free_block_bytes",
            "sse_clients",
            "sse_connections",
            "task_stack_high_water_marks",
        )
        return {name: deepcopy(parsed[name]) for name in names if name in parsed}

    def _cancel_active_stream(self) -> None:
        with self._lock:
            stream_stop = self._stream_stop
            thread = self._stream_thread
        if stream_stop is not None:
            stream_stop.set()
        self.client.close_stream()
        if thread is not None and thread is not current_thread():
            thread.join(timeout=self.client.sse_connect_timeout + 1.0)

    def _finish_if_complete(self) -> bool:
        with self._lock:
            if self._state["state"] in self.TERMINAL_STATES:
                return True
            thread = self._thread
            stream_thread = self._stream_thread
            if self._pending_terminal is None:
                return False
            if thread is not None and thread.is_alive():
                return False
            if stream_thread is not None and stream_thread.is_alive():
                return False
            if self.client.budget.active != 0:
                return False
            terminal = self._pending_terminal
            self._thread = None
            self._stream_thread = None
            self._stream_stop = None
            self._state["state"] = terminal
            self._state["active_churn_connections"] = 0
            self._state["cleanup_complete"] = True
            self._state["end_timestamp"] = datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            )
            if self._started_ns is not None:
                self._state["elapsed_ms"] = round(
                    (time.monotonic_ns() - self._started_ns) / 1_000_000, 3
                )
            record_terminal = not self._terminal_recorded
            self._terminal_recorded = True
            result = deepcopy(self._state)
        if record_terminal:
            self.recorder.append(
                f"churn_run_{terminal}",
                run_id=self.run_id,
                elapsed_ms=result["elapsed_ms"],
                cleanup_complete=True,
                summary=self._compact_summary(result),
            )
        return True

    def snapshot(self, recent_records: int = 100) -> dict[str, Any]:
        self._finish_if_complete()
        with self._lock:
            state = deepcopy(self._state)
        if self._started_ns is not None and state["state"] not in self.TERMINAL_STATES:
            state["elapsed_ms"] = round(
                (time.monotonic_ns() - self._started_ns) / 1_000_000, 3
            )
        state["active_device_connections"] = self.client.budget.active
        state["device_connection_limit"] = self.client.budget.limit
        state["recorder"] = self.recorder.summary()
        state["recent_records"] = self.recorder.snapshot()[-recent_records:]
        return state

    @staticmethod
    def _compact_summary(state: dict[str, Any]) -> dict[str, Any]:
        names = (
            "run_id",
            "state",
            "target",
            "configuration",
            "current_cycle",
            "total_cycles",
            "successful_connections",
            "rejected_connections",
            "http_failures",
            "transport_failures",
            "local_resource_failures",
            "remote_eof",
            "events_observed",
            "parse_failures",
            "boot_id_changed",
            "boot_id_changes",
            "initial_boot_id",
            "latest_boot_id",
            "cleanup_complete",
            "failure",
            "start_timestamp",
            "end_timestamp",
            "elapsed_ms",
        )
        return {name: deepcopy(state.get(name)) for name in names}

    def compact_summary(self) -> dict[str, Any]:
        return self._compact_summary(self.snapshot())
