"""Validated, local-only operator annotation requests."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import isfinite
import re
from typing import Any
from uuid import UUID


QUICK_MARKERS = (
    "fan_blocked",
    "fan_unblocked",
    "airflow_partial",
    "airflow_restored",
    "thermistor_unplugged",
    "thermistor_reconnected",
    "chamber_opened",
    "chamber_closed",
    "printer_stopped",
    "bed_target_changed",
    "printer_link_lost",
    "jumpjet_power_off",
    "jumpjet_power_on",
    "stimulus_applied",
    "stimulus_removed",
    "baseline_start",
    "recovery_start",
    "external_log_start",
    "scope_trigger",
    "flir_capture",
    "abort",
    "operator_intervention",
)
ANNOTATION_MARKERS = frozenset((*QUICK_MARKERS, "operator_note"))
RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
MAX_NOTE_CHARACTERS = 2_048
MAX_OPERATOR_CHARACTERS = 128
MAX_CORRELATION_TEXT_CHARACTERS = 512
MAX_KNOWN_OFFSET_MS = 1_000_000_000
MAX_CAPTURE_ANNOTATIONS = 1_000
CORRELATION_FIELDS = frozenset(
    {"instrument", "file_reference", "clock_sync_method", "known_offset_ms"}
)


class AnnotationProtocolError(RuntimeError):
    """A stable machine-readable annotation protocol failure."""

    code = "annotation_conflict"


class AnnotationConflictError(AnnotationProtocolError):
    """An annotation UUID was reused with different logical content."""


class AnnotationResolutionError(AnnotationProtocolError):
    """Previously submitted annotation evidence cannot be resolved uniquely."""

    code = "annotation_resolution_failed"


class AnnotationLimitError(AnnotationProtocolError):
    """The active capture has recorded its maximum annotation count."""

    code = "annotation_limit_reached"


class AnnotationIdentityMismatchError(AnnotationProtocolError):
    """Request capture identity does not match authoritative evidence."""

    code = "annotation_identity_mismatch"


class AnnotationNotRunningError(AnnotationProtocolError):
    """A new annotation was attempted outside a running capture."""

    code = "annotation_not_running"


def _validate_utf8_text(name: str, value: str) -> None:
    """Reject JSON strings that cannot be represented in persisted UTF-8."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must contain valid UTF-8 text") from exc


@dataclass(frozen=True, slots=True)
class AnnotationRequest:
    """Canonical content used for idempotent annotation insertion."""

    annotation_id: str
    run_id: str
    capture_session_id: str | None
    marker: str
    note: str
    operator: str | None
    # Correlation is compared as logical request content but omitted from the
    # generated hash because dict is intentionally mutable and unhashable.
    external_correlation: dict[str, Any] | None = field(hash=False)

    @classmethod
    def from_value(cls, value: object) -> "AnnotationRequest":
        if not isinstance(value, dict):
            raise ValueError("annotation must be a JSON object")
        allowed = {
            "annotation_id",
            "run_id",
            "capture_session_id",
            "marker",
            "note",
            "operator",
            "external_correlation",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown annotation field: {sorted(unknown)[0]}")

        annotation_id = value.get("annotation_id")
        if not isinstance(annotation_id, str):
            raise ValueError("annotation_id must be a UUID string")
        try:
            canonical_id = str(UUID(annotation_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError("annotation_id must be a UUID string") from exc
        if canonical_id != annotation_id.lower():
            raise ValueError("annotation_id must use canonical UUID form")

        run_id = value.get("run_id")
        if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise ValueError("run_id must identify the active capture")

        capture_session_id = value.get("capture_session_id")
        if capture_session_id is not None and (
            not isinstance(capture_session_id, str)
            or RUN_ID_PATTERN.fullmatch(capture_session_id) is None
        ):
            raise ValueError(
                "capture_session_id must identify persistent capture evidence"
            )

        marker = value.get("marker")
        if not isinstance(marker, str) or marker not in ANNOTATION_MARKERS:
            raise ValueError("marker is not a supported annotation type")

        note = value.get("note", "")
        if not isinstance(note, str):
            raise ValueError("note must be a string")
        if len(note) > MAX_NOTE_CHARACTERS:
            raise ValueError(
                f"note must not exceed {MAX_NOTE_CHARACTERS} characters"
            )
        _validate_utf8_text("note", note)
        if marker == "operator_note" and not note.strip():
            raise ValueError("operator_note requires a non-whitespace note")

        operator = value.get("operator")
        if operator is not None:
            if not isinstance(operator, str):
                raise ValueError("operator must be a string")
            if len(operator) > MAX_OPERATOR_CHARACTERS:
                raise ValueError(
                    f"operator must not exceed {MAX_OPERATOR_CHARACTERS} characters"
                )
            _validate_utf8_text("operator", operator)

        correlation = cls._validate_correlation(value.get("external_correlation"))
        return cls(
            annotation_id=canonical_id,
            run_id=run_id,
            capture_session_id=capture_session_id,
            marker=marker,
            note=note,
            operator=operator,
            external_correlation=correlation,
        )

    def for_capture(self, capture_session_id: str | None) -> "AnnotationRequest":
        """Bind client content to the authoritative active-capture identity."""
        return replace(self, capture_session_id=capture_session_id)

    def matches_record(self, record: object) -> bool:
        """Return whether durable evidence is the result of this exact request."""
        if not isinstance(record, dict):
            return False
        expected = {
            "annotation_id": self.annotation_id,
            "run_id": self.run_id,
            "capture_session_id": self.capture_session_id,
            "marker": self.marker,
            "note": self.note,
            "source": "operator",
            "operator": self.operator,
        }
        if any(record.get(name) != value for name, value in expected.items()):
            return False
        return record.get("external_correlation") == self.external_correlation

    @staticmethod
    def _validate_correlation(value: object) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("external_correlation must be a JSON object")
        unknown = set(value) - CORRELATION_FIELDS
        if unknown:
            raise ValueError(
                f"unknown external_correlation field: {sorted(unknown)[0]}"
            )
        result: dict[str, Any] = {}
        for name in ("instrument", "file_reference", "clock_sync_method"):
            item = value.get(name)
            if item is None:
                continue
            if not isinstance(item, str):
                raise ValueError(f"external_correlation.{name} must be a string")
            if len(item) > MAX_CORRELATION_TEXT_CHARACTERS:
                raise ValueError(
                    f"external_correlation.{name} must not exceed "
                    f"{MAX_CORRELATION_TEXT_CHARACTERS} characters"
                )
            _validate_utf8_text(f"external_correlation.{name}", item)
            result[name] = item
        offset = value.get("known_offset_ms")
        if offset is not None:
            try:
                offset_is_finite = (
                    not isinstance(offset, bool)
                    and isinstance(offset, (int, float))
                    and isfinite(float(offset))
                )
            except OverflowError:
                offset_is_finite = False
            if not offset_is_finite:
                raise ValueError(
                    "external_correlation.known_offset_ms must be a finite number"
                )
            if abs(offset) > MAX_KNOWN_OFFSET_MS:
                raise ValueError(
                    "external_correlation.known_offset_ms must be between "
                    f"-{MAX_KNOWN_OFFSET_MS} and {MAX_KNOWN_OFFSET_MS}"
                )
            result["known_offset_ms"] = offset
        return result or None
