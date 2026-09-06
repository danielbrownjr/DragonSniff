"""Bounded, read-only HTTP and SSE client for Dragon API v2."""

from __future__ import annotations

from contextlib import contextmanager
from http.client import HTTPException
import json
import socket
from threading import BoundedSemaphore, Event, Lock
import time
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .recording import SessionRecorder
from .target import DeviceTarget


JSON_ENDPOINTS = ("/api/v2/info", "/api/v2/state", "/api/v2/health")
EVENTS_ENDPOINT = "/api/v2/events"


class ResponseTooLargeError(RuntimeError):
    pass


class ConnectionBudget:
    """A visible fixed ceiling for concurrent device connections."""

    def __init__(self, limit: int = 2, acquire_timeout: float = 5.0) -> None:
        if limit < 1:
            raise ValueError("connection limit must be positive")
        if acquire_timeout < 0:
            raise ValueError("acquire timeout cannot be negative")
        self.limit = limit
        self.acquire_timeout = acquire_timeout
        self._semaphore = BoundedSemaphore(limit)
        self._active = 0
        self._lock = Lock()

    @contextmanager
    def lease(self) -> Iterator[None]:
        if not self._semaphore.acquire(timeout=self.acquire_timeout):
            raise TimeoutError("device connection budget was not available")
        with self._lock:
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
            self._semaphore.release()

    @property
    def active(self) -> int:
        with self._lock:
            return self._active


def _headers(response: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in response.headers.items()}


def _read_bounded(response: Any, limit: int) -> bytes:
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ResponseTooLargeError(f"response exceeded {limit} bytes")
    return body


def _decode(body: bytes) -> tuple[str, str | None]:
    try:
        return body.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        return body.decode("utf-8", errors="replace"), str(exc)


def _parse_json(raw: str) -> tuple[Any, str | None]:
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, str(exc)


def _response_socket(response: Any) -> socket.socket | None:
    """Return urllib's underlying socket when running on the supported CPython stack."""

    fp = getattr(response, "fp", None)
    raw = getattr(fp, "raw", None)
    candidate = getattr(raw, "_sock", None)
    return candidate if isinstance(candidate, socket.socket) else None


class DragonClient:
    """Observe only the fixed Dragon API endpoints; never issue device mutations."""

    def __init__(
        self,
        target: DeviceTarget,
        recorder: SessionRecorder,
        *,
        connection_limit: int = 2,
        request_timeout: float = 5.0,
        sse_connect_timeout: float = 5.0,
        max_response_bytes: int = 1_048_576,
        max_event_bytes: int = 262_144,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.target = target
        self.recorder = recorder
        self.budget = ConnectionBudget(connection_limit)
        self.request_timeout = request_timeout
        self.sse_connect_timeout = sse_connect_timeout
        self.max_response_bytes = max_response_bytes
        self.max_event_bytes = max_event_bytes
        self._opener = opener
        self._request_sequence = 0
        self._sequence_lock = Lock()
        self._stream_lock = Lock()
        self._stream_response: Any | None = None
        self._stream_socket: socket.socket | None = None

    def _request_id(self) -> int:
        with self._sequence_lock:
            self._request_sequence += 1
            return self._request_sequence

    @staticmethod
    def _request(url: str) -> Request:
        return Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json, text/event-stream",
                "Cache-Control": "no-store",
                "User-Agent": "DragonSniff/0.1",
            },
        )

    def fetch_json(
        self, path: str, *, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if path not in JSON_ENDPOINTS:
            raise ValueError("only the fixed read-only JSON endpoints are allowed")
        request_id = self._request_id()
        url = self.target.endpoint(path)
        started = time.monotonic_ns()
        record_context = dict(context or {})
        self.recorder.append(
            "http_request", **record_context, request_id=request_id, method="GET", endpoint=path
        )
        try:
            with self.budget.lease():
                with self._opener(self._request(url), timeout=self.request_timeout) as response:
                    status = response.status
                    headers = _headers(response)
                    body = _read_bounded(response, self.max_response_bytes)
            raw, decode_error = _decode(body)
            parsed, parse_error = _parse_json(raw)
            elapsed_ms = (time.monotonic_ns() - started) / 1_000_000
            result = {
                "request_id": request_id,
                "endpoint": path,
                "status": status,
                "elapsed_ms": round(elapsed_ms, 3),
                "headers": headers,
                "raw_payload": raw,
                "parsed": parsed,
                "decode_error": decode_error,
                "parse_error": parse_error,
                "ok": 200 <= status < 300 and parse_error is None,
            }
            self.recorder.append("http_response", **record_context, **result)
            return result
        except HTTPError as exc:
            body = exc.read(self.max_response_bytes + 1)
            too_large = len(body) > self.max_response_bytes
            if too_large:
                body = body[: self.max_response_bytes]
            raw, decode_error = _decode(body)
            parsed, parse_error = _parse_json(raw)
            if too_large:
                parsed = None
                parse_error = "response too large"
            result = {
                "request_id": request_id,
                "endpoint": path,
                "status": exc.code,
                "elapsed_ms": round((time.monotonic_ns() - started) / 1_000_000, 3),
                "headers": {name.lower(): value for name, value in exc.headers.items()},
                "raw_payload": raw,
                "parsed": parsed,
                "decode_error": decode_error,
                "parse_error": parse_error,
                "ok": False,
            }
            self.recorder.append("http_response", **record_context, **result)
            return result
        except (
            OSError,
            URLError,
            TimeoutError,
            ResponseTooLargeError,
            HTTPException,
        ) as exc:
            result = {
                "request_id": request_id,
                "endpoint": path,
                "status": None,
                "elapsed_ms": round((time.monotonic_ns() - started) / 1_000_000, 3),
                "error": f"{type(exc).__name__}: {exc}",
                "ok": False,
            }
            self.recorder.append("http_error", **record_context, **result)
            return result

    def stream_events(
        self,
        stop: Event,
        on_event: Callable[[dict[str, Any]], None],
        on_state: Callable[[str, dict[str, Any]], None],
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        request_id = self._request_id()
        path = EVENTS_ENDPOINT
        url = self.target.endpoint(path)
        started = time.monotonic_ns()
        record_context = dict(context or {})
        self.recorder.append(
            "sse_connecting", **record_context, request_id=request_id, endpoint=path
        )
        on_state("connecting", {**record_context, "request_id": request_id})
        exit_reason = "error"
        try:
            with self.budget.lease():
                try:
                    response = self._opener(self._request(url), timeout=self.sse_connect_timeout)
                except HTTPError as exc:
                    body = exc.read(self.max_response_bytes + 1)[: self.max_response_bytes]
                    raw, decode_error = _decode(body)
                    parsed, parse_error = _parse_json(raw)
                    details = {
                        **record_context,
                        "request_id": request_id,
                        "endpoint": path,
                        "status": exc.code,
                        "elapsed_ms": round((time.monotonic_ns() - started) / 1_000_000, 3),
                        "headers": {name.lower(): value for name, value in exc.headers.items()},
                        "raw_payload": raw,
                        "decode_error": decode_error,
                        "parsed": parsed,
                        "parse_error": parse_error,
                        "error": f"HTTP {exc.code}",
                    }
                    self.recorder.append("sse_unavailable", **details)
                    on_state("unavailable", details)
                    exit_reason = "unavailable"
                    return
                if stop.is_set():
                    response.close()
                    exit_reason = "stopped"
                    return
                with response:
                    stream_socket = _response_socket(response)
                    if stream_socket is not None:
                        # The timeout above bounds connection establishment only. SSE
                        # streams may be valid and indefinitely quiet, so established
                        # streams have no application-level inactivity deadline.
                        stream_socket.settimeout(None)
                    with self._stream_lock:
                        self._stream_response = response
                        self._stream_socket = stream_socket
                    details = {
                        **record_context,
                        "request_id": request_id,
                        "endpoint": path,
                        "status": response.status,
                        "elapsed_ms": round((time.monotonic_ns() - started) / 1_000_000, 3),
                        "headers": _headers(response),
                    }
                    self.recorder.append("sse_open", **details)
                    on_state("open", details)
                    for event in self._iter_sse(response, stop):
                        if stop.is_set():
                            break
                        dispatch = event.pop("dispatch")
                        comment_only = event.pop("comment_only")
                        kind = "sse_event" if dispatch else (
                            "sse_comment" if comment_only else "sse_ignored"
                        )
                        recorded = self.recorder.append(
                            kind, **record_context, request_id=request_id, **event
                        )
                        if dispatch:
                            on_event(recorded)
                    exit_reason = "stopped" if stop.is_set() else "end_of_stream"
        except (
            OSError,
            URLError,
            TimeoutError,
            ResponseTooLargeError,
            HTTPException,
            socket.timeout,
            ValueError,
            AttributeError,
        ) as exc:
            if stop.is_set():
                exit_reason = "stopped"
            else:
                exit_reason = "error"
                details = {
                    **record_context,
                    "request_id": request_id,
                    "endpoint": path,
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_ms": round((time.monotonic_ns() - started) / 1_000_000, 3),
                }
                self.recorder.append("sse_error", **details)
                on_state("error", details)
        finally:
            with self._stream_lock:
                self._stream_response = None
                self._stream_socket = None
            details = {
                **record_context,
                "request_id": request_id,
                "endpoint": path,
                "reason": exit_reason,
            }
            self.recorder.append("sse_closed", **details)
            on_state("closed", details)

    def close_stream(self) -> None:
        with self._stream_lock:
            response = self._stream_response
            stream_socket = self._stream_socket
        if stream_socket is not None:
            try:
                stream_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if response is not None:
            response.close()

    def _iter_sse(self, response: Any, stop: Event) -> Iterator[dict[str, Any]]:
        raw_lines: list[str] = []
        data_lines: list[str] = []
        event_name = "message"
        event_id: str | None = None
        size = 0
        while not stop.is_set():
            line_bytes = response.readline(self.max_event_bytes + 1)
            if not line_bytes:
                if raw_lines:
                    yield self._event(raw_lines, data_lines, event_name, event_id)
                return
            size += len(line_bytes)
            if size > self.max_event_bytes:
                raise ResponseTooLargeError(f"SSE event exceeded {self.max_event_bytes} bytes")
            line, _ = _decode(line_bytes.rstrip(b"\r\n"))
            if line == "":
                if raw_lines:
                    yield self._event(raw_lines, data_lines, event_name, event_id)
                raw_lines = []
                data_lines = []
                event_name = "message"
                event_id = None
                size = 0
                continue
            raw_lines.append(line)
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_lines.append(value)
            elif field == "event":
                event_name = value or "message"
            elif field == "id" and "\x00" not in value:
                event_id = value

    @staticmethod
    def _event(
        raw_lines: list[str], data_lines: list[str], event_name: str, event_id: str | None
    ) -> dict[str, Any]:
        data = "\n".join(data_lines)
        parsed: Any = None
        parse_error: str | None = None
        if data:
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError as exc:
                parse_error = str(exc)
        return {
            "event": event_name,
            "event_id": event_id,
            "raw_payload": "\n".join(raw_lines) + "\n\n",
            "data": data,
            "parsed": parsed,
            "parse_error": parse_error,
            "dispatch": bool(data_lines),
            "comment_only": bool(raw_lines) and all(line.startswith(":") for line in raw_lines),
        }
