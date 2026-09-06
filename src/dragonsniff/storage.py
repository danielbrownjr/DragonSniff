"""Durable, append-only evidence storage for unattended DragonSniff runs."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
from threading import Lock
from typing import Any
from uuid import uuid4

from .recording import SessionRecorder


FORMAT_VERSION = 1
DEFAULT_RETENTION_BYTES = 256 * 1024 * 1024
DEFAULT_RETENTION_SESSIONS = 500
SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
TERMINAL_RECORDS = {
    "session_stopped": "completed",
    "capture_run_completed": "completed",
    "capture_run_cancelled": "cancelled",
    "capture_run_failed": "failed",
    "churn_run_completed": "completed",
    "churn_run_cancelled": "cancelled",
    "churn_run_failed": "failed",
}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class PersistentSessionRecorder(SessionRecorder):
    """Keep the normal bounded live view while appending every record to disk."""

    def __init__(
        self,
        store: "SessionStore",
        session_id: str,
        max_records: int,
    ) -> None:
        super().__init__(max_records=max_records)
        self.store = store
        self.session_id = session_id

    def _before_record_visible(self, record: dict[str, Any]) -> None:
        try:
            self.store.append(self.session_id, record)
            terminal_status = TERMINAL_RECORDS.get(record["kind"])
            if terminal_status is not None:
                self.store.finish(self.session_id, terminal_status)
        except OSError as exc:
            raise RuntimeError(f"could not persist session evidence: {exc}") from exc

    def summary(self) -> dict[str, Any]:
        return {**super().summary(), "persistent_session_id": self.session_id}

    def abort_start(self, reason: str) -> None:
        self.store.finish(self.session_id, "failed", failure=reason)


class SessionStore:
    """Own a bounded directory of independently downloadable session evidence."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        retention_bytes: int = DEFAULT_RETENTION_BYTES,
        retention_sessions: int = DEFAULT_RETENTION_SESSIONS,
    ) -> None:
        if retention_bytes < 1 or retention_sessions < 1:
            raise ValueError("retention limits must be positive")
        self.root = Path(data_dir).expanduser().resolve()
        self.sessions_dir = self.root / "sessions"
        self.retention_bytes = retention_bytes
        self.retention_sessions = retention_sessions
        self._lock = Lock()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._recover_interrupted()
        self.enforce_retention()

    def create_recorder(
        self,
        kind: str,
        target: str,
        max_records: int,
    ) -> PersistentSessionRecorder:
        if kind not in {"observation", "capture", "churn"}:
            raise ValueError("unsupported persistent session kind")
        session_id = uuid4().hex
        now = _timestamp()
        metadata = {
            "format_version": FORMAT_VERSION,
            "session_id": session_id,
            "kind": kind,
            "target": target,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "finished_at": None,
            "records": 0,
            "bytes": 0,
        }
        with self._lock:
            session_dir = self._session_dir(session_id)
            session_dir.mkdir()
            (session_dir / "evidence.jsonl").touch(exist_ok=False)
            self._write_metadata_locked(session_id, metadata)
        return PersistentSessionRecorder(self, session_id, max_records)

    def append(self, session_id: str, record: dict[str, Any]) -> None:
        encoded = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        with self._lock:
            metadata = self._read_metadata_locked(session_id)
            if metadata["status"] != "active":
                raise RuntimeError("cannot append to a finished persistent session")
            evidence_path = self._evidence_path(session_id)
            descriptor = os.open(evidence_path, os.O_WRONLY | os.O_APPEND)
            try:
                written = os.write(descriptor, encoded)
                if written != len(encoded):
                    raise OSError("short write while persisting session evidence")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            metadata["records"] += 1
            metadata["bytes"] += len(encoded)
            metadata["updated_at"] = _timestamp()
            self._write_metadata_locked(session_id, metadata)

    def finish(
        self, session_id: str, status: str, *, failure: str | None = None
    ) -> None:
        if status not in {"completed", "cancelled", "failed"}:
            raise ValueError("invalid terminal session status")
        with self._lock:
            metadata = self._read_metadata_locked(session_id)
            if metadata["status"] == "active":
                now = _timestamp()
                metadata["status"] = status
                metadata["updated_at"] = now
                metadata["finished_at"] = now
                if failure is not None:
                    metadata["failure"] = failure
                self._write_metadata_locked(session_id, metadata)
        self.enforce_retention()

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = self._list_metadata_locked()
        sessions.sort(key=lambda item: item["created_at"], reverse=True)
        return deepcopy(sessions)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            return None
        with self._lock:
            try:
                return deepcopy(self._read_metadata_locked(session_id))
            except FileNotFoundError:
                return None

    def export_jsonl(self, session_id: str) -> bytes | None:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            return None
        with self._lock:
            path = self._evidence_path(session_id)
            try:
                return path.read_bytes()
            except FileNotFoundError:
                return None

    def evidence_path(self, session_id: str) -> Path | None:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            return None
        with self._lock:
            path = self._evidence_path(session_id)
            return path if path.is_file() and not path.is_symlink() else None

    def enforce_retention(self) -> None:
        with self._lock:
            sessions = self._list_metadata_locked()
            total = sum(self._directory_size(self._session_dir(item["session_id"])) for item in sessions)
            candidates = sorted(
                (item for item in sessions if item["status"] != "active"),
                key=lambda item: item["created_at"],
            )
            retained_count = len(sessions)
            for item in candidates:
                if (
                    total <= self.retention_bytes
                    and retained_count <= self.retention_sessions
                ):
                    break
                path = self._session_dir(item["session_id"])
                size = self._directory_size(path)
                shutil.rmtree(path)
                total -= size
                retained_count -= 1

    def _recover_interrupted(self) -> None:
        with self._lock:
            for metadata in self._list_metadata_locked():
                session_id = metadata["session_id"]
                self._repair_partial_tail_locked(session_id, metadata)
                if metadata["status"] == "active":
                    now = _timestamp()
                    metadata["status"] = "interrupted"
                    metadata["updated_at"] = now
                    metadata["finished_at"] = now
                    metadata["recovery"] = "active session found during service startup"
                    self._write_metadata_locked(session_id, metadata)

    def _repair_partial_tail_locked(
        self, session_id: str, metadata: dict[str, Any]
    ) -> None:
        path = self._evidence_path(session_id)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return
        valid = raw
        if raw and not raw.endswith(b"\n"):
            last_newline = raw.rfind(b"\n")
            valid = raw[: last_newline + 1] if last_newline >= 0 else b""
            partial = raw[len(valid) :]
            (path.parent / "evidence.partial").write_bytes(partial)
            path.write_bytes(valid)
            metadata["recovered_partial_bytes"] = len(partial)
        records = valid.count(b"\n")
        if metadata.get("records") != records or metadata.get("bytes") != len(valid):
            metadata["records"] = records
            metadata["bytes"] = len(valid)
        self._write_metadata_locked(session_id, metadata)

    def _list_metadata_locked(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for path in self.sessions_dir.iterdir():
            if (
                path.is_symlink()
                or not path.is_dir()
                or not SESSION_ID_PATTERN.fullmatch(path.name)
            ):
                continue
            try:
                result.append(self._read_metadata_locked(path.name))
            except (
                FileNotFoundError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
            ):
                continue
        return result

    def _read_metadata_locked(self, session_id: str) -> dict[str, Any]:
        path = self._session_dir(session_id) / "metadata.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("session_id") != session_id:
            raise ValueError("invalid session metadata")
        return value

    def _write_metadata_locked(self, session_id: str, metadata: dict[str, Any]) -> None:
        session_dir = self._session_dir(session_id)
        path = session_dir / "metadata.json"
        temporary = session_dir / "metadata.json.tmp"
        encoded = json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _session_dir(self, session_id: str) -> Path:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("invalid persistent session ID")
        return self.sessions_dir / session_id

    def _evidence_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "evidence.jsonl"

    @staticmethod
    def _directory_size(path: Path) -> int:
        return sum(item.stat().st_size for item in path.iterdir() if item.is_file())
