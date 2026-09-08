from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from dragonsniff.annotations import (
    AnnotationConflictError,
    AnnotationRequest,
    MAX_KNOWN_OFFSET_MS,
    QUICK_MARKERS,
)
from dragonsniff.capture import CaptureConfig, CaptureRunner
from dragonsniff.recording import SessionRecorder
from dragonsniff.server import SessionManager
from dragonsniff.storage import SessionStore
from dragonsniff.target import parse_target


def annotation_value(
    runner: CaptureRunner,
    marker: str = "baseline_start",
    *,
    annotation_id: str | None = None,
    note: str = "",
    operator: str | None = "Daniel",
    external_correlation: dict | None = None,
) -> dict:
    value = {
        "annotation_id": annotation_id or str(uuid4()),
        "run_id": runner.run_id,
        "capture_session_id": getattr(runner.recorder, "session_id", None),
        "marker": marker,
        "note": note,
        "operator": operator,
    }
    if external_correlation is not None:
        value["external_correlation"] = external_correlation
    return value


def active_runner(*, recorder: SessionRecorder | None = None, client=None) -> CaptureRunner:
    runner = CaptureRunner(
        parse_target("dragon.local"),
        CaptureConfig(duration_seconds=10, state_interval_seconds=1, health_interval_seconds=5),
        recorder=recorder,
        client=client,
    )
    runner._started_ns = time.monotonic_ns()
    runner._state["state"] = "running"
    return runner


class AnnotationTests(TestCase):
    def test_basic_ordering_with_concurrent_telemetry(self) -> None:
        runner = active_runner()
        first_written = Event()
        annotation_written = Event()

        def telemetry() -> None:
            runner.recorder.append("http_response", sample="before")
            first_written.set()
            self.assertTrue(annotation_written.wait(1))
            runner.recorder.append("http_response", sample="after")

        worker = Thread(target=telemetry)
        worker.start()
        self.assertTrue(first_written.wait(1))
        created, marker = runner.record_annotation(
            AnnotationRequest.from_value(annotation_value(runner, "baseline_start"))
        )
        annotation_written.set()
        worker.join(timeout=1)

        records = runner.recorder.snapshot()
        self.assertTrue(created)
        self.assertEqual(
            [record["kind"] for record in records],
            ["http_response", "operator_annotation", "http_response"],
        )
        self.assertEqual([record["sequence"] for record in records], [1, 2, 3])
        self.assertEqual(marker["sequence"], 2)
        self.assertGreaterEqual(marker["capture_relative_ms"], 0)
        self.assertIsNotNone(datetime.fromisoformat(marker["timestamp"]).tzinfo)

    def test_rapid_markers_have_no_loss_or_duplication(self) -> None:
        runner = active_runner()
        values = [
            annotation_value(runner, QUICK_MARKERS[index % len(QUICK_MARKERS)])
            for index in range(100)
        ]

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(
                pool.map(
                    lambda value: runner.record_annotation(
                        AnnotationRequest.from_value(value)
                    ),
                    values,
                )
            )

        records = [
            record
            for record in runner.recorder.snapshot()
            if record["kind"] == "operator_annotation"
        ]
        self.assertTrue(all(created for created, _ in results))
        self.assertEqual(len(records), 100)
        self.assertEqual(len({record["annotation_id"] for record in records}), 100)
        self.assertEqual(
            [record["sequence"] for record in records],
            sorted(record["sequence"] for record in records),
        )
        self.assertEqual(runner.snapshot()["annotation_count"], 100)

    def test_freeform_content_is_preserved_exactly(self) -> None:
        runner = active_runner()
        note = "  ΔT = 12.5 °C; fan @ 42% — 😀 e\u0301\n次の測定  "
        operator = "Dan / 現場 / Инженер"
        _, record = runner.record_annotation(
            AnnotationRequest.from_value(
                annotation_value(
                    runner,
                    "operator_note",
                    note=note,
                    operator=operator,
                )
            )
        )

        self.assertEqual(record["note"], note)
        self.assertEqual(record["operator"], operator)
        exported = runner.recorder.export_jsonl()
        decoded = json.loads(exported)
        self.assertEqual(decoded["note"], note)
        self.assertEqual(decoded["operator"], operator)

    def test_unencodable_text_is_rejected_without_damaging_capture(self) -> None:
        with TemporaryDirectory() as temporary:
            config = CaptureConfig(
                duration_seconds=10,
                state_interval_seconds=1,
                health_interval_seconds=5,
            )
            store = SessionStore(temporary)
            recorder = store.create_recorder(
                "capture",
                "http://dragon.local",
                config.estimated_records() + CaptureRunner.MAX_ANNOTATIONS,
            )
            runner = active_runner(recorder=recorder)
            invalid_values = (
                annotation_value(runner, "operator_note", note="bad \ud800"),
                annotation_value(runner, "operator_note", note="bad \udfff"),
                annotation_value(runner, "baseline_start", operator="bad \ud800"),
                annotation_value(
                    runner,
                    "scope_trigger",
                    external_correlation={"instrument": "bad \udfff"},
                ),
                annotation_value(
                    runner,
                    "scope_trigger",
                    external_correlation={"file_reference": "bad \ud800"},
                ),
                annotation_value(
                    runner,
                    "scope_trigger",
                    external_correlation={"clock_sync_method": "bad \udfff"},
                ),
            )
            for value in invalid_values:
                with self.subTest(value=value):
                    with self.assertRaisesRegex(ValueError, "valid UTF-8"):
                        runner.record_annotation(AnnotationRequest.from_value(value))

            telemetry = recorder.append("http_response", sample="capture healthy")
            records = recorder.snapshot()

        self.assertEqual(runner.snapshot()["state"], "running")
        self.assertEqual(runner.snapshot()["annotation_count"], 0)
        self.assertEqual(telemetry["kind"], "http_response")
        self.assertEqual([record["kind"] for record in records], ["http_response"])

    def test_duplicate_submission_is_exactly_once_and_content_bound(self) -> None:
        runner = active_runner()
        value = annotation_value(
            runner,
            "fan_blocked",
            note="mat placed at inlet",
        )
        request = AnnotationRequest.from_value(value)

        first_created, first = runner.record_annotation(request)
        retry_created, retry = runner.record_annotation(request)

        self.assertTrue(first_created)
        self.assertFalse(retry_created)
        self.assertEqual(first, retry)
        self.assertEqual(runner.snapshot()["annotation_count"], 1)
        self.assertEqual(runner.recorder.export_jsonl().count(value["annotation_id"]), 1)
        changed = AnnotationRequest.from_value({**value, "note": "different"})
        with self.assertRaisesRegex(AnnotationConflictError, "different content"):
            runner.record_annotation(changed)

    def test_null_capture_identity_is_normalized_before_idempotency_matching(self) -> None:
        with TemporaryDirectory() as temporary:
            config = CaptureConfig(
                duration_seconds=10,
                state_interval_seconds=1,
                health_interval_seconds=5,
            )
            store = SessionStore(temporary)
            recorder = store.create_recorder(
                "capture",
                "http://dragon.local",
                config.estimated_records() + CaptureRunner.MAX_ANNOTATIONS,
            )
            runner = active_runner(recorder=recorder)
            value = {
                **annotation_value(runner, "fan_blocked", note="50% obstruction"),
                "capture_session_id": None,
            }

            created, original = runner.record_annotation(
                AnnotationRequest.from_value(value)
            )
            retried, retry = runner.record_annotation(
                AnnotationRequest.from_value(value)
            )

            self.assertTrue(created)
            self.assertFalse(retried)
            self.assertEqual(retry, original)
            self.assertEqual(original["capture_session_id"], recorder.session_id)
            self.assertEqual(recorder.export_jsonl().count(value["annotation_id"]), 1)

            conflicting = AnnotationRequest.from_value({**value, "note": "changed"})
            with self.assertRaisesRegex(AnnotationConflictError, "different content"):
                runner.record_annotation(conflicting)

            stale = AnnotationRequest.from_value(
                {**value, "capture_session_id": uuid4().hex}
            )
            with self.assertRaisesRegex(AnnotationConflictError, "capture_session_id"):
                runner.record_annotation(stale)

    def test_persistent_restart_recovers_original_annotation_once(self) -> None:
        with TemporaryDirectory() as temporary:
            config = CaptureConfig(
                duration_seconds=10,
                state_interval_seconds=1,
                health_interval_seconds=5,
            )
            store = SessionStore(temporary)
            recorder = store.create_recorder(
                "capture",
                "http://dragon.local",
                config.estimated_records() + CaptureRunner.MAX_ANNOTATIONS,
            )
            runner = active_runner(recorder=recorder)
            value = annotation_value(
                runner,
                "scope_trigger",
                note="CH1 rising, 3.3 V",
            )
            _, original = runner.record_annotation(
                AnnotationRequest.from_value(value)
            )

            recovered = SessionStore(temporary)
            metadata = recovered.get_session(recorder.session_id)
            found = recovered.find_annotation(
                recorder.session_id, value["annotation_id"]
            )
            manager = SessionManager(store=recovered)
            created, retried, _ = manager.add_capture_annotation(value)
            with recovered.lease_evidence(recorder.session_id) as stream:
                records = [json.loads(line) for line in stream]

        self.assertEqual(metadata["status"], "interrupted")
        self.assertEqual(found, original)
        self.assertFalse(created)
        self.assertEqual(retried, original)
        self.assertEqual(
            sum(record.get("annotation_id") == value["annotation_id"] for record in records),
            1,
        )

    def test_unrecorded_interrupted_submission_resolves_not_recorded(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("capture", "http://dragon.local", 100)
            session_id = recorder.session_id
            recovered = SessionStore(temporary)
            manager = SessionManager(store=recovered)
            value = {
                "annotation_id": str(uuid4()),
                "run_id": uuid4().hex,
                "capture_session_id": session_id,
                "marker": "abort",
                "note": "",
                "operator": None,
            }

            with self.assertRaisesRegex(AnnotationConflictError, "was not recorded"):
                manager.add_capture_annotation(value)

    def test_retry_resolves_write_completed_before_local_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            config = CaptureConfig(
                duration_seconds=10,
                state_interval_seconds=1,
                health_interval_seconds=5,
            )
            store = SessionStore(temporary)
            recorder = store.create_recorder(
                "capture",
                "http://dragon.local",
                config.estimated_records() + CaptureRunner.MAX_ANNOTATIONS,
            )
            runner = active_runner(recorder=recorder)
            request = AnnotationRequest.from_value(
                annotation_value(runner, "stimulus_applied")
            )
            real_append = store.append

            def commit_then_fail(session_id, record):
                real_append(session_id, record)
                if record["kind"] == "operator_annotation":
                    raise OSError("simulated acknowledgement-window failure")

            with patch.object(store, "append", side_effect=commit_then_fail):
                with self.assertRaisesRegex(RuntimeError, "could not persist"):
                    runner.record_annotation(request)

            created, recovered = runner.record_annotation(request)
            records = [
                json.loads(line)
                for line in (
                    store.sessions_dir
                    / recorder.session_id
                    / "evidence.jsonl"
                ).read_bytes().splitlines()
            ]

        self.assertFalse(created)
        self.assertEqual(recovered["annotation_id"], request.annotation_id)
        self.assertEqual(
            sum(
                record.get("annotation_id") == request.annotation_id
                for record in records
            ),
            1,
        )

    def test_capture_boundaries_reject_non_running_and_stale_requests(self) -> None:
        runner = CaptureRunner(parse_target("dragon.local"), CaptureConfig())
        value = annotation_value(runner, "chamber_opened")
        request = AnnotationRequest.from_value(value)
        for state in ("idle", "stopping", "completed", "cancelled", "failed"):
            with self.subTest(state=state):
                runner._state["state"] = state
                with self.assertRaisesRegex(AnnotationConflictError, "only while"):
                    runner.record_annotation(request)

        runner._state["state"] = "running"
        stale = AnnotationRequest.from_value({**value, "run_id": uuid4().hex})
        with self.assertRaisesRegex(AnnotationConflictError, "run_id"):
            runner.record_annotation(stale)

    def test_annotation_limit_is_explicit_and_preserves_telemetry_capacity(self) -> None:
        runner = active_runner()
        values = [
            annotation_value(runner, annotation_id=str(uuid4()))
            for _ in range(CaptureRunner.MAX_ANNOTATIONS + 1)
        ]

        for value in values[: CaptureRunner.MAX_ANNOTATIONS]:
            created, _ = runner.record_annotation(AnnotationRequest.from_value(value))
            self.assertTrue(created)

        accepted_retry, accepted_record = runner.record_annotation(
            AnnotationRequest.from_value(values[-2])
        )
        self.assertFalse(accepted_retry)
        self.assertEqual(accepted_record["annotation_id"], values[-2]["annotation_id"])

        rejected = AnnotationRequest.from_value(values[-1])
        for _ in range(2):
            with self.assertRaisesRegex(AnnotationConflictError, "limit reached"):
                runner.record_annotation(rejected)

        telemetry = runner.recorder.append("http_response", sample="after limit")
        annotation_records = [
            record
            for record in runner.recorder.snapshot()
            if record["kind"] == "operator_annotation"
        ]
        self.assertEqual(len(annotation_records), CaptureRunner.MAX_ANNOTATIONS)
        self.assertEqual(
            runner.snapshot()["annotation_count"], CaptureRunner.MAX_ANNOTATIONS
        )
        self.assertEqual(runner.recorder.summary()["dropped_records"], 0)
        self.assertEqual(telemetry["kind"], "http_response")
        self.assertFalse(
            any(
                record.get("annotation_id") == rejected.annotation_id
                for record in annotation_records
            )
        )

    def test_external_clock_correlation_is_preserved_without_inference(self) -> None:
        runner = active_runner()
        correlation = {
            "instrument": "FLIR E8-XT",
            "file_reference": "IMG_0042.jpg",
            "clock_sync_method": "visible UTC clock frame",
            "known_offset_ms": -237.5,
        }
        _, record = runner.record_annotation(
            AnnotationRequest.from_value(
                annotation_value(
                    runner,
                    "flir_capture",
                    external_correlation=correlation,
                )
            )
        )

        self.assertEqual(record["external_correlation"], correlation)
        self.assertEqual(record["time_basis"], "operator_submission_received")
        self.assertNotIn("corrected_timestamp", record)

    def test_annotation_path_never_calls_the_device_client(self) -> None:
        recorder = SessionRecorder(max_records=2_000)

        class GuardClient:
            def __init__(self) -> None:
                self.recorder = recorder
                self.budget = SimpleNamespace(active=0, limit=1)
                self.calls = []

            def fetch_json(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                raise AssertionError("annotation attempted DUT traffic")

        client = GuardClient()
        runner = active_runner(client=client)
        runner.record_annotation(
            AnnotationRequest.from_value(
                annotation_value(runner, "operator_intervention")
            )
        )

        self.assertEqual(client.calls, [])
        self.assertEqual(runner.client.budget.active, 0)

    def test_quick_marker_contract_and_input_validation(self) -> None:
        required = {
            "fan_blocked", "fan_unblocked", "airflow_partial", "airflow_restored",
            "thermistor_unplugged", "thermistor_reconnected", "chamber_opened",
            "chamber_closed", "printer_stopped", "bed_target_changed",
            "printer_link_lost", "jumpjet_power_off", "jumpjet_power_on",
            "stimulus_applied", "stimulus_removed", "baseline_start",
            "recovery_start", "external_log_start", "scope_trigger",
            "flir_capture", "abort", "operator_intervention",
        }
        self.assertEqual(set(QUICK_MARKERS), required)
        runner = active_runner()
        with self.assertRaisesRegex(ValueError, "requires"):
            AnnotationRequest.from_value(annotation_value(runner, "operator_note"))
        with self.assertRaisesRegex(ValueError, "non-whitespace"):
            AnnotationRequest.from_value(
                annotation_value(runner, "operator_note", note=" \t\n ")
            )
        with self.assertRaisesRegex(ValueError, "finite number"):
            AnnotationRequest.from_value(
                annotation_value(
                    runner,
                    "scope_trigger",
                    external_correlation={"known_offset_ms": float("nan")},
                )
            )
        for offset in (-MAX_KNOWN_OFFSET_MS, MAX_KNOWN_OFFSET_MS):
            with self.subTest(offset=offset):
                request = AnnotationRequest.from_value(
                    annotation_value(
                        runner,
                        "scope_trigger",
                        external_correlation={"known_offset_ms": offset},
                    )
                )
                self.assertEqual(
                    request.external_correlation["known_offset_ms"], offset
                )
        for offset in (-MAX_KNOWN_OFFSET_MS - 1, MAX_KNOWN_OFFSET_MS + 1):
            with self.subTest(offset=offset):
                with self.assertRaisesRegex(ValueError, "must be between"):
                    AnnotationRequest.from_value(
                        annotation_value(
                            runner,
                            "scope_trigger",
                            external_correlation={"known_offset_ms": offset},
                        )
                    )

        request = AnnotationRequest.from_value(
            annotation_value(
                runner,
                external_correlation={"instrument": "hash safety"},
            )
        )
        self.assertIsInstance(hash(request), int)
        with self.assertRaisesRegex(ValueError, "finite number"):
            AnnotationRequest.from_value(
                annotation_value(
                    runner,
                    "scope_trigger",
                    external_correlation={"known_offset_ms": 10**10_000},
                )
            )
