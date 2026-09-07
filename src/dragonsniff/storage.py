"""Durable, append-only evidence storage for unattended DragonSniff runs."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
from threading import Lock
from typing import Any, BinaryIO, Iterator
from uuid import uuid4

from .recording import SessionRecorder


LOGGER = logging.getLogger(__name__)

FORMAT_VERSION = 1
DEFAULT_RETENTION_BYTES = 256 * 1024 * 1024
DEFAULT_RETENTION_SESSIONS = 500
METADATA_CHECKPOINT_RECORDS = 64
IO_CHUNK_BYTES = 64 * 1024
SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SAFE_FILENAME_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
SESSION_KINDS = frozenset({"observation", "capture", "churn"})
SESSION_STATUSES = frozenset(
    {"active", "completed", "cancelled", "failed", "interrupted"}
)
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


def is_valid_session_id(value: object) -> bool:
    """Return whether value is the one canonical persistent-session ID form."""
    return isinstance(value, str) and SESSION_ID_PATTERN.fullmatch(value) is not None


def export_filename(metadata: dict[str, Any]) -> str:
    """Build a header-safe filename from revalidated persistent metadata."""
    session_id = metadata.get("session_id")
    kind = metadata.get("kind")
    created_at = metadata.get("created_at")
    if (
        not is_valid_session_id(session_id)
        or kind not in SESSION_KINDS
        or not isinstance(created_at, str)
    ):
        raise ValueError("invalid session metadata for export")
    created = SAFE_FILENAME_COMPONENT_PATTERN.sub("-", created_at).strip(".-")
    if not created:
        created = "unknown-time"
    return f"dragonsniff-{kind}-{created}-{session_id[:8]}.jsonl"


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
            reason = f"could not persist session evidence: {exc}"
            try:
                self.fail(reason)
            except OSError:
                LOGGER.exception("could not persist failed session status")
            raise RuntimeError(reason) from exc

    def summary(self) -> dict[str, Any]:
        return {**super().summary(), "persistent_session_id": self.session_id}

    def fail(self, reason: str) -> None:
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
        self._metadata: dict[str, dict[str, Any]] = {}
        self._evidence_file_bytes: dict[str, int] = {}
        self._metadata_file_bytes: dict[str, int] = {}
        self._invalid_sessions: dict[str, str] = {}
        self._pending_checkpoints: dict[str, int] = {}
        self._download_leases: dict[str, int] = {}

        root_existed = self.root.exists()
        sessions_existed = self.sessions_dir.exists()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        if not root_existed:
            self._fsync_directory(self.root.parent)
        if not sessions_existed:
            self._fsync_directory(self.root)
        with self._lock:
            self._load_and_recover_locked()
        self.enforce_retention()

    def create_recorder(
        self,
        kind: str,
        target: str,
        max_records: int,
    ) -> PersistentSessionRecorder:
        if kind not in SESSION_KINDS:
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
            session_dir.mkdir(mode=0o700)
            self._fsync_directory(self.sessions_dir)
            evidence_path = self._evidence_path(session_id)
            descriptor = os.open(
                evidence_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._fsync_directory(session_dir)
            self._metadata[session_id] = metadata
            self._evidence_file_bytes[session_id] = 0
            self._pending_checkpoints[session_id] = 0
            self._write_metadata_locked(session_id, metadata)
        return PersistentSessionRecorder(self, session_id, max_records)

    def append(self, session_id: str, record: dict[str, Any]) -> None:
        encoded = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        with self._lock:
            metadata = self._metadata_for_locked(session_id)
            if metadata["status"] != "active":
                raise RuntimeError("cannot append to a finished persistent session")
            evidence_path = self._evidence_path(session_id)
            if evidence_path.is_symlink():
                raise OSError("persistent evidence path is a symbolic link")
            flags = os.O_WRONLY | os.O_APPEND
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(evidence_path, flags)
            try:
                self._write_all(descriptor, encoded)
                os.fsync(descriptor)
                evidence_file_bytes = os.fstat(descriptor).st_size
            finally:
                os.close(descriptor)
            self._evidence_file_bytes[session_id] = evidence_file_bytes
            metadata["records"] += 1
            metadata["bytes"] += len(encoded)
            metadata["updated_at"] = _timestamp()
            pending = self._pending_checkpoints.get(session_id, 0) + 1
            if pending >= METADATA_CHECKPOINT_RECORDS:
                self._write_metadata_locked(session_id, metadata)
                pending = 0
            self._pending_checkpoints[session_id] = pending

    def finish(
        self, session_id: str, status: str, *, failure: str | None = None
    ) -> None:
        if status not in {"completed", "cancelled", "failed"}:
            raise ValueError("invalid terminal session status")
        with self._lock:
            current = self._metadata_for_locked(session_id)
            metadata = deepcopy(current)
            changed = False
            if metadata["status"] == "active":
                now = _timestamp()
                metadata["status"] = status
                metadata["updated_at"] = now
                metadata["finished_at"] = now
                changed = True
            if failure is not None and metadata.get("failure") is None:
                metadata["failure"] = failure
                changed = True
            if changed or self._pending_checkpoints.get(session_id, 0):
                self._write_metadata_locked(session_id, metadata)
                self._metadata[session_id] = metadata
                self._pending_checkpoints[session_id] = 0
        self.enforce_retention()

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._metadata.values())
        sessions.sort(key=lambda item: item["created_at"], reverse=True)
        return deepcopy(sessions)

    def get_session(self, session_id: object) -> dict[str, Any] | None:
        if not is_valid_session_id(session_id):
            return None
        with self._lock:
            metadata = self._metadata.get(session_id)
            return deepcopy(metadata) if metadata is not None else None

    def storage_summary(self) -> dict[str, int]:
        """Report all canonical retained storage, including invalid sessions."""
        with self._lock:
            objects = self._storage_objects_locked()
        valid = [item for item in objects if item["valid"]]
        invalid = [item for item in objects if not item["valid"]]
        return {
            "retained_sessions": len(objects),
            "retained_bytes": sum(item["bytes"] for item in objects),
            "valid_sessions": len(valid),
            "valid_bytes": sum(item["bytes"] for item in valid),
            "invalid_sessions": len(invalid),
            "invalid_bytes": sum(item["bytes"] for item in invalid),
        }

    @contextmanager
    def lease_evidence(self, session_id: object) -> Iterator[BinaryIO | None]:
        """Open evidence while preventing retention from deleting its session."""
        stream: BinaryIO | None = None
        canonical_id: str | None = None
        if is_valid_session_id(session_id):
            with self._lock:
                if session_id in self._metadata:
                    path = self._evidence_path(session_id)
                    if path.is_file() and not path.is_symlink():
                        stream = path.open("rb")
                        canonical_id = session_id
                        self._download_leases[session_id] = (
                            self._download_leases.get(session_id, 0) + 1
                        )
        try:
            yield stream
        finally:
            if stream is not None:
                stream.close()
            if canonical_id is not None:
                with self._lock:
                    remaining = self._download_leases.get(canonical_id, 1) - 1
                    if remaining > 0:
                        self._download_leases[canonical_id] = remaining
                    else:
                        self._download_leases.pop(canonical_id, None)
                self.enforce_retention()

    def enforce_retention(self) -> None:
        with self._lock:
            objects = self._storage_objects_locked()
            total = sum(item["bytes"] for item in objects)
            candidates = sorted(
                (
                    item
                    for item in objects
                    if not item["active"]
                ),
                key=lambda item: (item["order"], item["session_id"]),
            )
            retained_count = len(objects)
            removed = False
            for item in candidates:
                if (
                    total <= self.retention_bytes
                    and retained_count <= self.retention_sessions
                ):
                    break
                session_id = item["session_id"]
                if self._download_leases.get(session_id):
                    continue
                path = self._session_dir(session_id)
                if not item["valid"]:
                    LOGGER.warning(
                        "removing invalid retained session %s to satisfy retention",
                        session_id,
                    )
                try:
                    shutil.rmtree(path)
                except OSError as exc:
                    LOGGER.warning(
                        "could not remove retained session %s; will retry later: %s",
                        session_id,
                        exc,
                    )
                    break
                self._metadata.pop(session_id, None)
                self._evidence_file_bytes.pop(session_id, None)
                self._metadata_file_bytes.pop(session_id, None)
                self._invalid_sessions.pop(session_id, None)
                self._pending_checkpoints.pop(session_id, None)
                total -= item["bytes"]
                retained_count -= 1
                removed = True
            if removed:
                self._fsync_directory(self.sessions_dir)

    def _load_and_recover_locked(self) -> None:
        for path in self.sessions_dir.iterdir():
            if path.is_symlink() or not path.is_dir() or not is_valid_session_id(path.name):
                continue
            try:
                metadata = self._read_metadata_file_locked(path.name)
                metadata_file_bytes = (path / "metadata.json").stat().st_size
            except (
                OSError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                self._register_invalid_locked(path.name, exc)
                continue
            session_id = metadata["session_id"]
            self._metadata_file_bytes[session_id] = metadata_file_bytes
            evidence_changed = self._repair_partial_tail_locked(session_id, metadata)
            evidence_bytes = self._evidence_size_locked(session_id)
            self._evidence_file_bytes[session_id] = evidence_bytes
            if (
                metadata["status"] == "active"
                or evidence_changed
                or not self._evidence_bytes_match_metadata(metadata, evidence_bytes)
            ):
                records, size = self._scan_evidence_locked(session_id)
                metadata_changed = (
                    metadata.get("records") != records
                    or metadata.get("bytes") != size
                )
                metadata["records"] = records
                metadata["bytes"] = size
            else:
                metadata_changed = False
            if metadata["status"] == "active":
                now = _timestamp()
                metadata["status"] = "interrupted"
                metadata["updated_at"] = now
                metadata["finished_at"] = now
                metadata["recovery"] = "active session found during service startup"
                metadata_changed = True
            self._metadata[session_id] = metadata
            self._pending_checkpoints[session_id] = 0
            if metadata_changed or evidence_changed:
                self._write_metadata_locked(session_id, metadata)

    def _storage_objects_locked(self) -> list[dict[str, Any]]:
        self._discover_invalid_sessions_locked()
        objects = [
            {
                "session_id": metadata["session_id"],
                "bytes": self._valid_storage_bytes_locked(metadata),
                "order": metadata["created_at"],
                "valid": True,
                "active": metadata["status"] == "active",
            }
            for metadata in self._metadata.values()
        ]
        for session_id in self._invalid_sessions:
            path = self._session_dir(session_id)
            if path.is_dir() and not path.is_symlink():
                objects.append(
                    {
                        "session_id": session_id,
                        "bytes": self._directory_size(path),
                        "order": self._filesystem_order(path),
                        "valid": False,
                        "active": False,
                    }
                )
        return objects

    def _valid_storage_bytes_locked(self, metadata: dict[str, Any]) -> int:
        """Return exact application-owned bytes without walking a valid session."""
        session_id = metadata["session_id"]
        return (
            self._evidence_file_bytes[session_id]
            + self._metadata_file_bytes[session_id]
            + metadata.get("recovered_partial_bytes", 0)
        )

    @staticmethod
    def _evidence_bytes_match_metadata(
        metadata: dict[str, Any], evidence_file_bytes: int
    ) -> bool:
        logical_bytes = metadata["bytes"]
        if evidence_file_bytes == logical_bytes:
            return True
        # Existing Windows files use CRLF because the low-level descriptors were
        # opened without O_BINARY; each JSONL record contributes one extra byte.
        return os.name == "nt" and evidence_file_bytes == (
            logical_bytes + metadata["records"]
        )

    def _discover_invalid_sessions_locked(self) -> None:
        present: set[str] = set()
        for path in self.sessions_dir.iterdir():
            if path.is_symlink() or not path.is_dir() or not is_valid_session_id(path.name):
                continue
            session_id = path.name
            if session_id in self._metadata:
                continue
            present.add(session_id)
            if session_id not in self._invalid_sessions:
                self._register_invalid_locked(
                    session_id,
                    ValueError("canonical session directory was not loaded at startup"),
                )
        for session_id in set(self._invalid_sessions) - present:
            self._invalid_sessions.pop(session_id, None)

    def _register_invalid_locked(self, session_id: str, exc: Exception) -> None:
        reason = f"{type(exc).__name__}: {exc}"
        self._invalid_sessions[session_id] = reason
        LOGGER.warning(
            "retaining invalid session %s for storage accounting: %s",
            session_id,
            reason,
        )

    @staticmethod
    def _filesystem_order(path: Path) -> str:
        """Use directory mtime only as an invalid-session retention fallback."""
        try:
            modified = path.stat().st_mtime
        except OSError:
            return ""
        return datetime.fromtimestamp(modified, timezone.utc).isoformat()

    def _repair_partial_tail_locked(
        self, session_id: str, metadata: dict[str, Any]
    ) -> bool:
        path = self._evidence_path(session_id)
        try:
            stream = path.open("r+b")
        except FileNotFoundError:
            return False
        with stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            if size == 0:
                return False
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) == b"\n":
                return False
            valid_size = self._last_complete_record_end(stream, size)
            partial_size = size - valid_size
            partial_path = path.parent / "evidence.partial"
            with partial_path.open("wb") as partial:
                stream.seek(valid_size)
                while chunk := stream.read(IO_CHUNK_BYTES):
                    partial.write(chunk)
                partial.flush()
                os.fsync(partial.fileno())
            stream.seek(valid_size)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        self._fsync_directory(path.parent)
        metadata["recovered_partial_bytes"] = partial_size
        return True

    @staticmethod
    def _last_complete_record_end(stream: BinaryIO, size: int) -> int:
        position = size
        while position > 0:
            start = max(0, position - IO_CHUNK_BYTES)
            stream.seek(start)
            chunk = stream.read(position - start)
            newline = chunk.rfind(b"\n")
            if newline >= 0:
                return start + newline + 1
            position = start
        return 0

    def _scan_evidence_locked(self, session_id: str) -> tuple[int, int]:
        path = self._evidence_path(session_id)
        records = 0
        size = 0
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(IO_CHUNK_BYTES):
                    records += chunk.count(b"\n")
                    size += len(chunk)
        except FileNotFoundError:
            return 0, 0
        return records, size

    def _read_metadata_file_locked(self, session_id: str) -> dict[str, Any]:
        path = self._session_dir(session_id) / "metadata.json"
        if path.is_symlink():
            raise ValueError("invalid session metadata")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid session metadata")
        if value.get("session_id") != session_id or not is_valid_session_id(session_id):
            raise ValueError("session metadata ID does not match its directory")
        if value.get("format_version") != FORMAT_VERSION:
            raise ValueError(
                f'unsupported session metadata format version: {value.get("format_version")!r}'
            )
        if value.get("kind") not in SESSION_KINDS:
            raise ValueError("invalid session metadata kind")
        if value.get("status") not in SESSION_STATUSES:
            raise ValueError("invalid session metadata status")
        for name in ("target", "created_at", "updated_at"):
            if not isinstance(value.get(name), str):
                raise ValueError("invalid session metadata")
        if not isinstance(value.get("records"), int) or value["records"] < 0:
            raise ValueError("invalid session metadata")
        if not isinstance(value.get("bytes"), int) or value["bytes"] < 0:
            raise ValueError("invalid session metadata")
        recovered = value.get("recovered_partial_bytes", 0)
        if not isinstance(recovered, int) or recovered < 0:
            raise ValueError("invalid recovered partial byte count")
        return value

    def _evidence_size_locked(self, session_id: str) -> int:
        try:
            return self._evidence_path(session_id).stat().st_size
        except FileNotFoundError:
            return 0

    def _metadata_for_locked(self, session_id: object) -> dict[str, Any]:
        if not is_valid_session_id(session_id):
            raise ValueError("invalid persistent session ID")
        try:
            return self._metadata[session_id]
        except KeyError as exc:
            raise FileNotFoundError("unknown persistent session") from exc

    def _write_metadata_locked(self, session_id: str, metadata: dict[str, Any]) -> None:
        session_dir = self._session_dir(session_id)
        path = session_dir / "metadata.json"
        temporary = session_dir / f"metadata.{uuid4().hex}.tmp"
        encoded = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        metadata_file_bytes = 0
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                self._write_all(descriptor, encoded)
                os.fsync(descriptor)
                metadata_file_bytes = os.fstat(descriptor).st_size
            finally:
                os.close(descriptor)
            os.replace(temporary, path)
            self._fsync_directory(session_dir)
            self._metadata_file_bytes[session_id] = metadata_file_bytes
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _session_dir(self, session_id: object) -> Path:
        if not is_valid_session_id(session_id):
            raise ValueError("invalid persistent session ID")
        return self.sessions_dir / session_id

    def _evidence_path(self, session_id: object) -> Path:
        return self._session_dir(session_id) / "evidence.jsonl"

    @staticmethod
    def _directory_size(path: Path) -> int:
        total = 0
        pending = [path]
        while pending:
            current = pending.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                try:
                    total += current.stat().st_size
                except OSError:
                    pass
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
        return total

    @staticmethod
    def _write_all(descriptor: int, value: bytes) -> None:
        written = 0
        while written < len(value):
            count = os.write(descriptor, value[written:])
            if count <= 0:
                raise OSError("short write while persisting session evidence")
            written += count

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
