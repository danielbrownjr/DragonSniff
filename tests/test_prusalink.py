from __future__ import annotations

from io import BytesIO
from http.client import BadStatusLine
import json
from tempfile import TemporaryDirectory
from unittest import TestCase
from urllib.error import HTTPError, URLError

from dragonsniff.prusalink import (
    PRUSALINK_STATUS_PATH,
    PrusaLinkConfig,
    PrusaLinkConfigError,
    PrusaLinkSource,
)
from dragonsniff.recording import SessionRecorder
from dragonsniff.storage import SessionStore


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.status = status
        self.headers: dict[str, str] = {"Content-Type": "application/json"}
        self._body = BytesIO(body)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


class Clock:
    def __init__(self) -> None:
        self.nanoseconds = 0

    def __call__(self) -> int:
        return self.nanoseconds


def status_body(
    *, state: str = "PRINTING", bed: object = 61.25, target: object = 65
) -> bytes:
    return json.dumps(
        {
            "printer": {
                "state": state,
                "temp_bed": bed,
                "target_bed": target,
                "temp_nozzle": 219.5,
                "target_nozzle": 220,
                "ignored": "not admitted to the normalized data",
            }
        },
        ensure_ascii=False,
    ).encode("utf-8")


class PrusaLinkConfigTests(TestCase):
    def test_source_is_disabled_by_default(self) -> None:
        config = PrusaLinkConfig.from_values(None, None)
        recorder = SessionRecorder(10)
        source = PrusaLinkSource(config, recorder)

        self.assertFalse(config.enabled)
        self.assertEqual(config.public_snapshot()["state"], "disabled")
        self.assertNotIn("api_key", config.public_snapshot())
        self.assertNotIn(
            "secret",
            repr(PrusaLinkConfig.from_values("http://prusa.local", "secret", 5)),
        )
        self.assertFalse(source.start())
        self.assertEqual(recorder.snapshot(), [])

    def test_configuration_rejects_unsafe_or_incomplete_values(self) -> None:
        invalid = (
            (None, "secret", 5),
            ("http://user:secret@printer.local", "secret", 5),
            ("http://printer.local/path", "secret", 5),
            ("http://printer.local", "line\nbreak", 5),
            ("http://printer.local", "secret", 0.99),
            ("http://printer.local", "secret", 61),
        )
        for url, key, interval in invalid:
            with self.subTest(url=url, interval=interval):
                with self.assertRaises(PrusaLinkConfigError):
                    PrusaLinkConfig.from_values(url, key, interval)


class PrusaLinkSourceTests(TestCase):
    def setUp(self) -> None:
        self.config = PrusaLinkConfig.from_values(
            "http://prusa.local", "top-secret-key", 5
        )
        self.recorder = SessionRecorder(100)

    def test_valid_authenticated_response_records_only_one_fixed_get(self) -> None:
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            return FakeResponse(status_body(state="FINISHED"))

        source = PrusaLinkSource(self.config, self.recorder, opener=opener)
        record = source.poll_once(context={"owner": "capture", "run_id": "run-1"})

        request, timeout = requests[0]
        self.assertEqual(request.full_url, "http://prusa.local/api/v1/status")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("X-api-key"), "top-secret-key")
        self.assertEqual(timeout, 5.0)
        self.assertEqual(record["kind"], "source_observation")
        self.assertEqual(record["source"], "prusalink")
        self.assertEqual(record["source_id"], "http://prusa.local")
        self.assertEqual(record["endpoint"], PRUSALINK_STATUS_PATH)
        self.assertEqual(record["owner"], "capture")
        self.assertEqual(record["run_id"], "run-1")
        self.assertEqual(record["status"], "healthy")
        self.assertEqual(record["freshness"]["state"], "fresh")
        self.assertEqual(
            record["data"],
            {
                "printer_state": "FINISHED",
                "bed_temperature_c": 61.25,
                "bed_target_c": 65.0,
                "nozzle_temperature_c": 219.5,
                "nozzle_target_c": 220.0,
            },
        )
        self.assertNotIn("top-secret-key", json.dumps(record))
        self.assertNotIn("top-secret-key", json.dumps(source.snapshot()))

    def test_auth_failure_is_explicit_and_does_not_expose_credentials(self) -> None:
        def opener(request, *, timeout):
            raise HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                {"Content-Type": "application/json"},
                BytesIO(b'{"message":"unauthorized"}'),
            )

        source = PrusaLinkSource(self.config, self.recorder, opener=opener)
        record = source.poll_once()

        self.assertEqual(record["status"], "auth_error")
        self.assertFalse(record["authenticated"])
        self.assertTrue(record["connected"])
        self.assertEqual(record["error"]["kind"], "authentication")
        self.assertEqual(source.snapshot()["state"], "auth_error")
        self.assertNotIn("top-secret-key", json.dumps(record))

    def test_transport_failure_is_evidence_and_secret_is_redacted(self) -> None:
        def opener(request, *, timeout):
            raise URLError(f"connection failed with top-secret-key at {request.full_url}")

        source = PrusaLinkSource(self.config, self.recorder, opener=opener)
        record = source.poll_once()

        self.assertEqual(record["status"], "transport_error")
        self.assertFalse(record["connected"])
        self.assertEqual(record["freshness"]["state"], "unavailable")
        self.assertIn("<redacted>", record["error"]["message"])
        self.assertNotIn("top-secret-key", json.dumps(record))

    def test_malformed_http_status_line_is_contained_as_transport_evidence(self) -> None:
        source = PrusaLinkSource(
            self.config,
            self.recorder,
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                BadStatusLine("not HTTP")
            ),
        )

        record = source.poll_once()

        self.assertEqual(record["status"], "transport_error")
        self.assertEqual(record["error"]["kind"], "transport")

    def test_oversized_response_preserves_http_reachability(self) -> None:
        source = PrusaLinkSource(
            self.config,
            self.recorder,
            opener=lambda *_args, **_kwargs: FakeResponse(b"x" * 17),
            max_response_bytes=16,
        )

        record = source.poll_once()

        self.assertEqual(record["status"], "parse_error")
        self.assertEqual(record["error"]["kind"], "response_too_large")
        self.assertTrue(record["response_too_large"])
        self.assertTrue(record["connected"])
        self.assertTrue(record["authenticated"])
        self.assertEqual(record["response_status"], 200)
        self.assertIsNone(record["data"])

    def test_syntax_missing_fields_and_nonfinite_numbers_are_parse_errors(self) -> None:
        bodies = (
            (b"{bad", "syntax"),
            (b'{"printer":{"state":"IDLE"}}', "schema"),
            (b'{"printer":{"state":"IDLE","temp_bed":NaN,"target_bed":0}}', "schema"),
            (b'{"printer":{"state":"IDLE","temp_bed":true,"target_bed":0}}', "schema"),
        )
        for body, kind in bodies:
            with self.subTest(body=body):
                source = PrusaLinkSource(
                    self.config,
                    SessionRecorder(10),
                    opener=lambda *_args, **_kwargs: FakeResponse(body),
                )
                record = source.poll_once()
                self.assertEqual(record["status"], "parse_error")
                self.assertEqual(record["parse_error_kind"], kind)
                self.assertIsNone(record["data"])

    def test_unsafe_decoded_unicode_never_enters_source_evidence(self) -> None:
        source = PrusaLinkSource(
            self.config,
            self.recorder,
            opener=lambda *_args, **_kwargs: FakeResponse(
                b'{"printer":{"state":"\\ud800","temp_bed":20,"target_bed":0}}'
            ),
        )

        record = source.poll_once()

        self.assertEqual(record["status"], "parse_error")
        self.assertEqual(record["parse_error_kind"], "unsafe_text")
        json.dumps(record, ensure_ascii=False).encode("utf-8")

    def test_unexpected_state_and_valid_unicode_are_preserved_as_observation(self) -> None:
        state = "未来状態 🐉 e\u0301"
        source = PrusaLinkSource(
            self.config,
            self.recorder,
            opener=lambda *_args, **_kwargs: FakeResponse(status_body(state=state)),
        )

        record = source.poll_once()

        self.assertEqual(record["status"], "healthy")
        self.assertEqual(record["data"]["printer_state"], state)

    def test_device_cannot_echo_the_api_key_into_evidence(self) -> None:
        source = PrusaLinkSource(
            self.config,
            self.recorder,
            opener=lambda *_args, **_kwargs: FakeResponse(
                status_body(state="top-secret-key")
            ),
        )

        record = source.poll_once()

        self.assertEqual(record["status"], "parse_error")
        self.assertEqual(record["parse_error_kind"], "credential_echo")
        self.assertNotIn("top-secret-key", json.dumps(record))

    def test_stale_state_is_explicit_and_a_new_good_sample_recovers(self) -> None:
        clock = Clock()
        responses = [FakeResponse(status_body()), URLError("offline"), FakeResponse(status_body(state="IDLE", target=0))]

        def opener(*_args, **_kwargs):
            response = responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

        source = PrusaLinkSource(
            self.config, self.recorder, opener=opener, monotonic_ns=clock
        )
        source.poll_once()
        clock.nanoseconds = 16_000_000_000

        self.assertEqual(source.snapshot()["freshness"]["state"], "stale")
        self.assertEqual(source.snapshot()["state"], "stale")
        failed = source.poll_once()
        self.assertEqual(failed["status"], "transport_error")
        self.assertEqual(failed["freshness"]["state"], "stale")

        clock.nanoseconds = 17_000_000_000
        recovered = source.poll_once()
        self.assertEqual(recovered["status"], "healthy")
        self.assertEqual(recovered["freshness"]["state"], "fresh")
        self.assertEqual(source.snapshot()["data"]["printer_state"], "IDLE")
        self.assertIsNone(source.snapshot()["last_error"])

    def test_failure_backoff_is_bounded_and_never_busy_loops(self) -> None:
        source = PrusaLinkSource(self.config, self.recorder)

        self.assertEqual(source.retry_delay_seconds(0), 5)
        self.assertEqual(source.retry_delay_seconds(1), 5)
        self.assertEqual(source.retry_delay_seconds(2), 10)
        self.assertEqual(source.retry_delay_seconds(5), 60)
        self.assertEqual(source.retry_delay_seconds(100), 60)

    def test_persistent_source_record_recovers_and_exports(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("capture", "http://dragon.local", 20)
            source = PrusaLinkSource(
                self.config,
                recorder,
                opener=lambda *_args, **_kwargs: FakeResponse(status_body()),
            )
            source.poll_once(
                context={
                    "owner": "capture",
                    "run_id": "run-1",
                    "capture_session_id": recorder.session_id,
                }
            )
            recorder.append("capture_run_completed")
            session_id = recorder.session_id

            recovered = SessionStore(temporary)
            with recovered.lease_evidence(session_id) as stream:
                assert stream is not None
                records = [json.loads(line) for line in stream]

        self.assertEqual(records[0]["kind"], "source_observation")
        self.assertEqual(records[0]["source"], "prusalink")
        self.assertEqual(records[0]["capture_session_id"], session_id)
        self.assertEqual(recovered.get_session(session_id)["status"], "completed")
