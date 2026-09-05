"""Bounded passive polling for thermal-controller validation evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from math import ceil
from threading import Event, Lock, Thread, current_thread
import time
from typing import Any
from uuid import uuid4

from .client import DragonClient
from .recording import SessionRecorder
from .target import DeviceTarget


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    """A bounded polling schedule that cannot exceed the recorder budget."""

    duration_seconds: float = 120.0
    state_interval_seconds: float = 1.0
    health_interval_seconds: float = 10.0

    MIN_DURATION_SECONDS = 1.0
    MAX_DURATION_SECONDS = 43_200.0
    MIN_STATE_INTERVAL_SECONDS = 0.5
    MAX_STATE_INTERVAL_SECONDS = 60.0
    MIN_HEALTH_INTERVAL_SECONDS = 5.0
    MAX_HEALTH_INTERVAL_SECONDS = 300.0
    MAX_ESTIMATED_RECORDS = 25_000

    @classmethod
    def profiles(cls) -> dict[str, "CaptureConfig"]:
        return {
            "Smoke": cls(120.0, 1.0, 10.0),
            "Soak": cls(900.0, 2.0, 30.0),
            "Extended": cls(1_800.0, 5.0, 60.0),
            "Long Haul": cls(28_800.0, 5.0, 60.0),
        }

    @classmethod
    def profile_snapshots(cls) -> dict[str, dict[str, float]]:
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
    def from_value(cls, value: object) -> "CaptureConfig":
        if not isinstance(value, dict):
            raise ValueError("configuration must be a JSON object")
        allowed = {
            "duration_seconds",
            "state_interval_seconds",
            "health_interval_seconds",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown configuration field: {sorted(unknown)[0]}")
        defaults = cls()
        values = {
            name: value.get(name, getattr(defaults, name))
            for name in allowed
        }
        for name, item in values.items():
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError(f"{name} must be a number")
        result = cls(**{name: float(item) for name, item in values.items()})
        result.validate()
        return result

    def validate(self) -> None:
        bounds = (
            (
                self.MIN_DURATION_SECONDS <= self.duration_seconds <= self.MAX_DURATION_SECONDS,
                "duration_seconds",
                self.MIN_DURATION_SECONDS,
                self.MAX_DURATION_SECONDS,
            ),
            (
                self.MIN_STATE_INTERVAL_SECONDS
                <= self.state_interval_seconds
                <= self.MAX_STATE_INTERVAL_SECONDS,
                "state_interval_seconds",
                self.MIN_STATE_INTERVAL_SECONDS,
                self.MAX_STATE_INTERVAL_SECONDS,
            ),
            (
                self.MIN_HEALTH_INTERVAL_SECONDS
                <= self.health_interval_seconds
                <= self.MAX_HEALTH_INTERVAL_SECONDS,
                "health_interval_seconds",
                self.MIN_HEALTH_INTERVAL_SECONDS,
                self.MAX_HEALTH_INTERVAL_SECONDS,
            ),
        )
        for accepted, name, lower, upper in bounds:
            if not accepted:
                raise ValueError(f"{name} must be between {lower} and {upper}")
        if self.estimated_records() > self.MAX_ESTIMATED_RECORDS:
            raise ValueError(
                "capture schedule exceeds the retained-record budget; increase an interval "
                "or reduce duration"
            )

    def estimated_records(self) -> int:
        # Each fetch records one request and one response/error. Include start/end
        # identity snapshots and controller lifecycle records, then round upward.
        state_samples = ceil(self.duration_seconds / self.state_interval_seconds) + 2
        health_samples = ceil(self.duration_seconds / self.health_interval_seconds) + 2
        return (state_samples + health_samples + 2) * 2 + 4

    def snapshot(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def bounds(cls) -> dict[str, Any]:
        return {
            "duration_seconds": {
                "min": cls.MIN_DURATION_SECONDS,
                "max": cls.MAX_DURATION_SECONDS,
            },
            "state_interval_seconds": {
                "min": cls.MIN_STATE_INTERVAL_SECONDS,
                "max": cls.MAX_STATE_INTERVAL_SECONDS,
            },
            "health_interval_seconds": {
                "min": cls.MIN_HEALTH_INTERVAL_SECONDS,
                "max": cls.MAX_HEALTH_INTERVAL_SECONDS,
            },
            "max_estimated_records": cls.MAX_ESTIMATED_RECORDS,
        }


class CaptureRunner:
    """Poll fixed read-only endpoints on a deterministic bounded schedule."""

    TERMINAL_STATES = {"completed", "cancelled", "failed"}

    def __init__(
        self,
        target: DeviceTarget,
        config: CaptureConfig,
        *,
        recorder: SessionRecorder | None = None,
        client: DragonClient | None = None,
    ) -> None:
        config.validate()
        self.target = target
        self.config = config
        required_records = config.estimated_records()
        self.recorder = (
            client.recorder
            if client is not None
            else (recorder or SessionRecorder(max_records=required_records))
        )
        if self.recorder.max_records < required_records:
            raise ValueError(
                "capture recorder is smaller than the estimated record requirement"
            )
        self.client = client or DragonClient(target, self.recorder, connection_limit=1)
        self.run_id = uuid4().hex
        self._lock = Lock()
        self._cancel = Event()
        self._thread: Thread | None = None
        self._started_ns: int | None = None
        self._state: dict[str, Any] = {
            "state": "idle",
            "run_id": self.run_id,
            "target": target.base_url,
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
        }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._state["state"] != "idle":
                raise RuntimeError("capture run has already started")
            self._state["state"] = "running"
            self._state["cleanup_complete"] = False
            self._started_ns = time.monotonic_ns()
            started = self.recorder.append(
                "capture_run_started",
                run_id=self.run_id,
                target=self.target.base_url,
                configuration=self.config.snapshot(),
                profile=self.config.profile_name(),
                bounds=self.config.bounds(),
                estimated_records=self.config.estimated_records(),
            )
            self._state["start_timestamp"] = started["timestamp"]
            thread = Thread(
                target=self._run,
                name=f"dragonsniff-capture-{self.run_id[:8]}",
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
        self._cancel.set()
        if thread is not None and thread is not current_thread():
            thread.join(timeout=max(0.0, timeout))
        return self._finish_if_complete()

    def _run(self) -> None:
        outcome = "completed"
        try:
            self._sample("/api/v2/info", "start")
            started = time.monotonic()
            deadline = started + self.config.duration_seconds
            next_state = started
            next_health = started
            while not self._cancel.is_set():
                now = time.monotonic()
                if now >= deadline:
                    break
                if now >= next_state:
                    self._sample("/api/v2/state", "scheduled")
                    next_state += self.config.state_interval_seconds
                    if next_state <= now:
                        next_state = now + self.config.state_interval_seconds
                now = time.monotonic()
                if now >= next_health and not self._cancel.is_set():
                    self._sample("/api/v2/health", "scheduled")
                    next_health += self.config.health_interval_seconds
                    if next_health <= now:
                        next_health = now + self.config.health_interval_seconds
                wait_for = max(0.0, min(deadline, next_state, next_health) - time.monotonic())
                self._cancel.wait(wait_for)
            if self._cancel.is_set():
                outcome = "cancelled"
            else:
                self._sample("/api/v2/state", "end")
                self._sample("/api/v2/health", "end")
                self._sample("/api/v2/info", "end")
        except Exception as exc:
            outcome = "cancelled" if self._cancel.is_set() else "failed"
            details = {"type": type(exc).__name__, "message": str(exc)}
            with self._lock:
                self._state["failure"] = details
            self.recorder.append("capture_internal_failure", run_id=self.run_id, **details)
        finally:
            self._complete(outcome)

    def _sample(self, path: str, sample_point: str) -> None:
        with self._lock:
            fetch_sequence = self._state["fetches_completed"] + 1
        context = {
            "run_id": self.run_id,
            "owner": "capture",
            "fetch_sequence": fetch_sequence,
            "sample_point": sample_point,
        }
        result = self.client.fetch_json(path, context=context)
        with self._lock:
            self._state["fetches_completed"] = fetch_sequence
            if path == "/api/v2/info":
                self._state["latest_info"] = deepcopy(result)
            elif path == "/api/v2/state":
                self._state["latest_state"] = deepcopy(result)
                key = "state_successes" if result["ok"] else "state_failures"
                self._state[key] += 1
                self._state["samples_completed"] += 1
            elif path == "/api/v2/health":
                self._state["latest_health"] = deepcopy(result)
                key = "health_successes" if result["ok"] else "health_failures"
                self._state[key] += 1
                self._observe_boot_id(result, fetch_sequence)

    def _observe_boot_id(
        self, result: dict[str, Any], fetch_sequence: int
    ) -> None:
        parsed = result.get("parsed")
        boot_id = parsed.get("boot_id") if isinstance(parsed, dict) else None
        if not isinstance(boot_id, (str, int)):
            return
        boot_id = str(boot_id)
        previous = self._state["latest_boot_id"]
        if self._state["initial_boot_id"] is None:
            self._state["initial_boot_id"] = boot_id
        elif previous is not None and previous != boot_id:
            self._state["boot_id_changed"] = True
            self._state["boot_id_changes"].append(
                {
                    "from": previous,
                    "to": boot_id,
                    "fetch_sequence": fetch_sequence,
                }
            )
        self._state["latest_boot_id"] = boot_id

    def _complete(self, outcome: str) -> None:
        with self._lock:
            final = "cancelled" if self._cancel.is_set() else outcome
            self._state["state"] = final
            self._state["cleanup_complete"] = self.client.budget.active == 0
            elapsed_ms = self._elapsed_ms()
            self._state["elapsed_ms"] = elapsed_ms
            samples_completed = self._state["samples_completed"]
            fetches_completed = self._state["fetches_completed"]
        record = self.recorder.append(
            f"capture_run_{final}",
            run_id=self.run_id,
            samples_completed=samples_completed,
            fetches_completed=fetches_completed,
            elapsed_ms=elapsed_ms,
        )
        with self._lock:
            self._state["end_timestamp"] = record["timestamp"]

    def _finish_if_complete(self) -> bool:
        with self._lock:
            thread = self._thread
            if thread is not None and thread.is_alive():
                return False
            if self.client.budget.active != 0:
                return False
            if self._state["state"] == "stopping":
                return False
            self._state["cleanup_complete"] = True
            return self._state["state"] in self.TERMINAL_STATES

    def snapshot(self, recent_records: int = 100) -> dict[str, Any]:
        self._finish_if_complete()
        with self._lock:
            state = deepcopy(self._state)
            if self._started_ns is not None and state["state"] in {"running", "stopping"}:
                state["elapsed_ms"] = self._elapsed_ms()
        state.update(
            {
                "active_device_connections": self.client.budget.active,
                "device_connection_limit": self.client.budget.limit,
                "recorder": self.recorder.summary(),
                "recent_records": self.recorder.snapshot()[-recent_records:],
            }
        )
        return state

    def _elapsed_ms(self) -> float:
        if self._started_ns is None:
            return 0.0
        return round((time.monotonic_ns() - self._started_ns) / 1_000_000, 3)
