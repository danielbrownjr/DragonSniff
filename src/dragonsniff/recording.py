"""Thread-safe, bounded in-memory diagnostic session recording."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import json
from threading import Lock
import time
from typing import Any


class SessionRecorder:
    """Store raw observations in arrival order and export them as JSONL."""

    def __init__(self, max_records: int = 2_000) -> None:
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self.max_records = max_records
        self._records: deque[dict[str, Any]] = deque(maxlen=max_records)
        self._next_sequence = 1
        self._dropped = 0
        self._lock = Lock()

    def append(self, kind: str, **fields: Any) -> dict[str, Any]:
        if not kind:
            raise ValueError("kind is required")
        record = {
            "sequence": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "monotonic_ns": time.monotonic_ns(),
            "kind": kind,
            **deepcopy(fields),
        }
        with self._lock:
            record["sequence"] = self._next_sequence
            self._next_sequence += 1
            if len(self._records) == self.max_records:
                self._dropped += 1
            self._records.append(record)
        return deepcopy(record)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(list(self._records))

    def summary(self) -> dict[str, int]:
        with self._lock:
            return {
                "records": len(self._records),
                "max_records": self.max_records,
                "dropped_records": self._dropped,
            }

    def export_jsonl(self) -> str:
        records = self.snapshot()
        return "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in records
        )
