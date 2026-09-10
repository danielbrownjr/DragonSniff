"""Optional, read-only PrusaLink observation source."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.client import HTTPException
import math
import re
from threading import Event, Lock, Thread
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ._version import __version__
from .client import (
    ResponseTooLargeError,
    _decode,
    _parse_decoded_json,
    _read_bounded,
)
from .recording import SessionRecorder
from .target import TargetValidationError, parse_target


PRUSALINK_STATUS_PATH = "/api/v1/status"
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
MIN_POLL_INTERVAL_SECONDS = 1.0
MAX_POLL_INTERVAL_SECONDS = 60.0
MAX_RETRY_INTERVAL_SECONDS = 60.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 64 * 1024
CAPTURE_BOUNDARY_REQUEST_SECONDS = 20.0
API_KEY_PATTERN = re.compile(r"[!-~]{1,256}\Z")


class PrusaLinkConfigError(ValueError):
    """Raised when startup configuration is not safe or complete."""


class PrusaLinkPayloadError(ValueError):
    """Raised when a response is JSON but not a usable status sample."""


@dataclass(frozen=True, slots=True)
class PrusaLinkConfig:
    """Validated startup-only configuration; the API key is never serialized."""

    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS

    @classmethod
    def from_values(
        cls,
        base_url: object,
        api_key: object,
        poll_interval_seconds: object = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> "PrusaLinkConfig":
        supplied_url = base_url.strip() if isinstance(base_url, str) else base_url
        supplied_key = api_key if isinstance(api_key, str) else api_key
        if supplied_url is None or supplied_url == "":
            if supplied_key is not None and supplied_key != "":
                raise PrusaLinkConfigError(
                    "PrusaLink URL is required when an API key is configured"
                )
            return cls()
        if not isinstance(supplied_url, str):
            raise PrusaLinkConfigError("PrusaLink URL must be text")
        try:
            target = parse_target(supplied_url)
        except TargetValidationError as exc:
            raise PrusaLinkConfigError(f"invalid PrusaLink URL: {exc}") from exc
        if not isinstance(supplied_key, str) or API_KEY_PATTERN.fullmatch(supplied_key) is None:
            raise PrusaLinkConfigError(
                "PrusaLink API key must contain 1-256 printable ASCII characters"
            )
        if isinstance(poll_interval_seconds, bool):
            raise PrusaLinkConfigError("PrusaLink poll interval must be numeric")
        try:
            interval = float(poll_interval_seconds)
        except (TypeError, ValueError) as exc:
            raise PrusaLinkConfigError("PrusaLink poll interval must be numeric") from exc
        if not math.isfinite(interval) or not (
            MIN_POLL_INTERVAL_SECONDS <= interval <= MAX_POLL_INTERVAL_SECONDS
        ):
            raise PrusaLinkConfigError(
                "PrusaLink poll interval must be between 1 and 60 seconds"
            )
        return cls(target.base_url, supplied_key, interval)

    @property
    def enabled(self) -> bool:
        return self.base_url is not None

    @property
    def endpoint_url(self) -> str:
        if self.base_url is None:
            raise RuntimeError("PrusaLink source is disabled")
        return f"{self.base_url}{PRUSALINK_STATUS_PATH}"

    @property
    def source_id(self) -> str | None:
        return self.base_url

    @property
    def stale_after_seconds(self) -> float:
        return min(180.0, max(15.0, self.poll_interval_seconds * 3.0))

    def estimated_capture_records(
        self,
        duration_seconds: float,
        boundary_request_seconds: float = CAPTURE_BOUNDARY_REQUEST_SECONDS,
    ) -> int:
        if not self.enabled:
            return 0
        return (
            math.ceil(
                (duration_seconds + boundary_request_seconds)
                / self.poll_interval_seconds
            )
            + 2
        )

    def public_snapshot(self, *, state: str | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {
                "configured": False,
                "state": "disabled",
                "source": "prusalink",
                "source_id": None,
            }
        return {
            "configured": True,
            "state": state or "configured",
            "source": "prusalink",
            "source_id": self.source_id,
            "endpoint": PRUSALINK_STATUS_PATH,
            "poll_interval_seconds": self.poll_interval_seconds,
            "stale_after_seconds": self.stale_after_seconds,
            "connected": False,
            "authenticated": None,
            "freshness": {"state": "unavailable", "sample_age_ms": None},
            "data": None,
            "last_error": None,
        }


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PrusaLinkPayloadError(f"printer.{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise PrusaLinkPayloadError(f"printer.{name} must be a finite number")
    return result


def parse_prusalink_status(value: object) -> dict[str, Any]:
    """Admit only the small, documented status subset used as evidence."""
    if not isinstance(value, dict):
        raise PrusaLinkPayloadError("PrusaLink status must be a JSON object")
    printer = value.get("printer")
    if not isinstance(printer, dict):
        raise PrusaLinkPayloadError("PrusaLink status must contain printer object")
    state = printer.get("state")
    if not isinstance(state, str) or not state:
        raise PrusaLinkPayloadError("printer.state must be non-empty text")
    result: dict[str, Any] = {
        "printer_state": state,
        "bed_temperature_c": _finite_number(printer.get("temp_bed"), "temp_bed"),
        "bed_target_c": _finite_number(printer.get("target_bed"), "target_bed"),
    }
    if "temp_nozzle" in printer:
        result["nozzle_temperature_c"] = _finite_number(
            printer["temp_nozzle"], "temp_nozzle"
        )
    if "target_nozzle" in printer:
        result["nozzle_target_c"] = _finite_number(
            printer["target_nozzle"], "target_nozzle"
        )
    return result


class PrusaLinkSource:
    """Poll one fixed PrusaLink GET endpoint into a shared session recorder."""

    def __init__(
        self,
        config: PrusaLinkConfig,
        recorder: SessionRecorder,
        *,
        opener: Callable[..., Any] = urlopen,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        timestamp: Callable[[], str] = _utc_timestamp,
    ) -> None:
        self.config = config
        self.recorder = recorder
        self.request_timeout = request_timeout
        self.max_response_bytes = max_response_bytes
        self._opener = opener
        self._monotonic_ns = monotonic_ns
        self._timestamp = timestamp
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._context: dict[str, Any] = {}
        self._running = False
        self._state = config.public_snapshot()
        self._last_good_monotonic_ns: int | None = None

    @staticmethod
    def _request(config: PrusaLinkConfig) -> Request:
        if config.api_key is None:
            raise RuntimeError("PrusaLink source is disabled")
        return Request(
            config.endpoint_url,
            method="GET",
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-store",
                "User-Agent": f"DragonSniff/{__version__}",
                "X-Api-Key": config.api_key,
            },
        )

    def start(self, *, context: dict[str, Any] | None = None) -> bool:
        if not self.config.enabled:
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._context = dict(context or {})
            self._running = True
            self._state["state"] = "connecting"
            thread = Thread(
                target=self._run,
                name="dragonsniff-prusalink",
                daemon=True,
            )
            self._thread = thread
        thread.start()
        return True

    def request_stop(self) -> None:
        self._stop.set()

    def stop(self, timeout: float = 6.0) -> bool:
        self.request_stop()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))
        return thread is None or not thread.is_alive()

    @property
    def is_alive(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        failures = 0
        try:
            while not self._stop.is_set():
                result = self.poll_once(context=self._context)
                failures = 0 if result["status"] == "healthy" else failures + 1
                if self._stop.wait(self.retry_delay_seconds(failures)):
                    break
        finally:
            with self._lock:
                self._running = False

    def retry_delay_seconds(self, consecutive_failures: int) -> float:
        if consecutive_failures <= 0:
            return self.config.poll_interval_seconds
        exponent = min(consecutive_failures - 1, 10)
        return min(
            MAX_RETRY_INTERVAL_SECONDS,
            self.config.poll_interval_seconds * (2**exponent),
        )

    def poll_once(
        self, *, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.config.enabled:
            raise RuntimeError("PrusaLink source is disabled")
        observed_ns = self._monotonic_ns()
        observed_at = self._timestamp()
        started_ns = observed_ns
        response_status: int | None = None
        fields: dict[str, Any]
        try:
            with self._opener(
                self._request(self.config), timeout=self.request_timeout
            ) as response:
                response_status = response.status
                body = _read_bounded(response, self.max_response_bytes)
            raw, decode_error = _decode(body)
            parsed, parse_error, parse_error_kind = _parse_decoded_json(
                raw, decode_error
            )
            if not 200 <= response_status < 300:
                fields = self._http_failure_fields(response_status, decode_error)
            elif decode_error is not None:
                fields = self._parse_failure_fields(
                    response_status, decode_error, None, "decode"
                )
            elif parse_error_kind is not None:
                fields = self._parse_failure_fields(
                    response_status, None, parse_error, parse_error_kind
                )
            else:
                try:
                    data = parse_prusalink_status(parsed)
                except PrusaLinkPayloadError as exc:
                    fields = self._parse_failure_fields(
                        response_status, None, str(exc), "schema"
                    )
                else:
                    if (
                        self.config.api_key is not None
                        and self.config.api_key in data["printer_state"]
                    ):
                        fields = self._parse_failure_fields(
                            response_status,
                            None,
                            "PrusaLink response overlaps configured credential",
                            "credential_echo",
                        )
                    else:
                        fields = {
                            "status": "healthy",
                            "connected": True,
                            "authenticated": True,
                            "response_status": response_status,
                            "decode_error": None,
                            "parse_error": None,
                            "parse_error_kind": None,
                            "data": data,
                            "error": None,
                        }
        except HTTPError as exc:
            try:
                body = exc.read(self.max_response_bytes + 1)
            except (OSError, HTTPException) as body_error:
                fields = self._error_fields(
                    "transport_error", "transport", body_error
                )
            else:
                too_large = len(body) > self.max_response_bytes
                _raw, decode_error = _decode(body[: self.max_response_bytes])
                fields = self._http_failure_fields(exc.code, decode_error)
                if too_large:
                    fields["response_too_large"] = True
        except ResponseTooLargeError as exc:
            fields = self._response_too_large_fields(response_status, exc)
        except (OSError, URLError, TimeoutError, HTTPException) as exc:
            fields = self._error_fields("transport_error", "transport", exc)

        completed_ns = self._monotonic_ns()
        with self._lock:
            last_good_ns = self._last_good_monotonic_ns
        freshness = self._freshness_for(
            completed_ns,
            completed_ns if fields["status"] == "healthy" else last_good_ns,
        )
        record = self.recorder.append(
            "source_observation",
            **dict(context or {}),
            source="prusalink",
            source_id=self.config.source_id,
            endpoint=PRUSALINK_STATUS_PATH,
            method="GET",
            observed_at=observed_at,
            observed_monotonic_ns=observed_ns,
            elapsed_ms=round((completed_ns - started_ns) / 1_000_000, 3),
            freshness=freshness,
            **fields,
        )
        with self._lock:
            if fields["status"] == "healthy":
                self._last_good_monotonic_ns = completed_ns
                self._state["data"] = deepcopy(fields["data"])
                self._state["last_success_timestamp"] = record["timestamp"]
                self._state["last_error"] = None
            else:
                self._state["last_error"] = {
                    "kind": fields.get("error", {}).get("kind"),
                    "message": fields.get("error", {}).get("message"),
                    "timestamp": record["timestamp"],
                }
            self._state.update(
                {
                    "state": fields["status"],
                    "connected": fields["connected"],
                    "authenticated": fields["authenticated"],
                    "freshness": freshness,
                    "last_observation_timestamp": record["timestamp"],
                }
            )
        return record

    def _http_failure_fields(
        self, status_code: int, decode_error: str | None
    ) -> dict[str, Any]:
        auth_error = status_code in {401, 403}
        return {
            "status": "auth_error" if auth_error else "transport_error",
            "connected": True,
            "authenticated": False if auth_error else None,
            "response_status": status_code,
            "decode_error": decode_error,
            "parse_error": None,
            "parse_error_kind": None,
            "data": None,
            "response_too_large": False,
            "error": {
                "kind": "authentication" if auth_error else "http_status",
                "message": (
                    "PrusaLink authentication was rejected"
                    if auth_error
                    else f"PrusaLink returned HTTP {status_code}"
                ),
            },
        }

    @staticmethod
    def _parse_failure_fields(
        status_code: int,
        decode_error: str | None,
        parse_error: str | None,
        parse_error_kind: str,
    ) -> dict[str, Any]:
        message = decode_error or parse_error or "PrusaLink response was not usable JSON"
        return {
            "status": "parse_error",
            "connected": True,
            "authenticated": True,
            "response_status": status_code,
            "decode_error": decode_error,
            "parse_error": parse_error,
            "parse_error_kind": parse_error_kind,
            "data": None,
            "response_too_large": False,
            "error": {"kind": parse_error_kind, "message": message[:512]},
        }

    def _error_fields(
        self, status: str, kind: str, exc: BaseException
    ) -> dict[str, Any]:
        message = f"{type(exc).__name__}: {exc}"
        if self.config.api_key:
            message = message.replace(self.config.api_key, "<redacted>")
        return {
            "status": status,
            "connected": False,
            "authenticated": None,
            "response_status": None,
            "decode_error": None,
            "parse_error": None,
            "parse_error_kind": None,
            "data": None,
            "response_too_large": kind == "response_too_large",
            "error": {"kind": kind, "message": message[:512]},
        }

    def _response_too_large_fields(
        self, response_status: int | None, exc: ResponseTooLargeError
    ) -> dict[str, Any]:
        fields = self._error_fields("parse_error", "response_too_large", exc)
        fields.update(
            {
                "connected": response_status is not None,
                "authenticated": True if response_status is not None else None,
                "response_status": response_status,
            }
        )
        return fields

    def _freshness_for(
        self, now_ns: int, last_good_monotonic_ns: int | None
    ) -> dict[str, Any]:
        if last_good_monotonic_ns is None:
            return {"state": "unavailable", "sample_age_ms": None}
        age_ms = max(0.0, (now_ns - last_good_monotonic_ns) / 1_000_000)
        return {
            "state": (
                "stale"
                if age_ms > self.config.stale_after_seconds * 1_000
                else "fresh"
            ),
            "sample_age_ms": round(age_ms, 3),
        }

    def snapshot(self) -> dict[str, Any]:
        now_ns = self._monotonic_ns()
        with self._lock:
            result = deepcopy(self._state)
            result["polling"] = self._running
            last_good_ns = self._last_good_monotonic_ns
        result["freshness"] = self._freshness_for(now_ns, last_good_ns)
        if result["state"] == "healthy" and result["freshness"]["state"] == "stale":
            result["state"] = "stale"
        return result
