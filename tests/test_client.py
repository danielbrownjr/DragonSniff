from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import IncompleteRead
import json
from threading import Event, Thread
import time
from unittest import TestCase

from dragonsniff.client import MAX_PARSED_JSON_DEPTH, ConnectionBudget, DragonClient
from dragonsniff.recording import SessionRecorder
from dragonsniff.target import parse_target


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        config.setdefault("request_log", []).append(("GET", self.path))
        bad_status_once = config.get("bad_status_once", set())
        if self.path in bad_status_once:
            bad_status_once.remove(self.path)
            self.close_connection = True
            self.wfile.write(b"11\r\n")
            self.wfile.flush()
            return
        if self.path == "/api/v2/events":
            if config.get("events_connect_delay"):
                time.sleep(config["events_connect_delay"])
            if config.get("events_status", 200) != 200:
                body = config.get(
                    "events_error_body", b'{"error":"busy","future_detail":true}'
                )
                self.send_response(config["events_status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if config.get("quiet_seconds"):
                time.sleep(config["quiet_seconds"])
                return
            try:
                self.wfile.write(b": connected\n\n")
                event_blocks = config.get("event_blocks")
                if event_blocks is not None:
                    for block in event_blocks:
                        self.wfile.write(block)
                else:
                    event_data = config.get("event_data", '{"known":1,"unknown":2}')
                    for index in range(config.get("event_count", 1)):
                        self.wfile.write(
                            f"event: telemetry\nid: {index}\ndata: {event_data}\n\n".encode()
                        )
                self.wfile.flush()
            except OSError:
                pass
            return
        if self.path == "/api/v2/health" and config.get("health_sequence"):
            sequence = config["health_sequence"]
            body = sequence.pop(0) if len(sequence) > 1 else sequence[0]
        else:
            body = config.get(self.path, b'{"api_version":2}')
        delays = config.get("json_delay_seconds", {})
        if self.path in delays:
            time.sleep(delays[self.path])
        statuses = config.get("json_status", {})
        self.send_response(statuses.get(self.path, 200))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        config.setdefault("request_log", []).append(("POST", self.path))
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()


class DeviceFixture:
    def __init__(self, config: dict[str, object] | None = None) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.server.config = config or {}  # type: ignore[attr-defined]
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "DeviceFixture":
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    @property
    def target(self) -> str:
        return f"127.0.0.1:{self.server.server_port}"


class BlockingTeardownResponse:
    status = 200
    headers = {"Content-Type": "text/event-stream"}

    def __init__(self) -> None:
        self.read_started = Event()
        self.closed = Event()

    def __enter__(self) -> "BlockingTeardownResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def readline(self, limit: int) -> bytes:
        self.read_started.set()
        self.closed.wait(timeout=1)
        raise AttributeError("'NoneType' object has no attribute 'peek'")

    def close(self) -> None:
        self.closed.set()


class IncompleteReadResponse:
    status = 200
    headers = {"Content-Type": "text/event-stream"}

    def __enter__(self) -> "IncompleteReadResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def readline(self, limit: int) -> bytes:
        raise IncompleteRead(b"partial")


class ClientTests(TestCase):
    def test_json_is_schema_free_and_raw_payload_is_preserved(self) -> None:
        raw = b'{"api_version":2,"product_specific":{"future":[1,2]},"optional":null}'
        with DeviceFixture({"/api/v2/info": raw}) as fixture:
            recorder = SessionRecorder()
            client = DragonClient(parse_target(fixture.target), recorder)
            result = client.fetch_json("/api/v2/info")

        self.assertTrue(result["ok"])
        self.assertEqual(result["raw_payload"], raw.decode())
        self.assertEqual(result["parsed"]["product_specific"]["future"], [1, 2])
        self.assertIsNone(result["parsed"]["optional"])
        self.assertEqual(client.budget.active, 0)

    def test_decoded_json_requires_recursively_utf8_encodable_text(self) -> None:
        cases = {
            "/api/v2/info": b'{"value":"\\ud800"}',
            "/api/v2/state": b'{"value":"\\udc00"}',
            "/api/v2/health": b'{"outer":{"items":["ok","\\ud800"]}}',
        }
        with DeviceFixture(dict(cases)) as fixture:
            recorder = SessionRecorder()
            client = DragonClient(parse_target(fixture.target), recorder)
            results = {path: client.fetch_json(path) for path in tuple(cases)}

        for path, raw in cases.items():
            with self.subTest(path=path):
                result = results[path]
                self.assertFalse(result["ok"])
                self.assertEqual(result["raw_payload"], raw.decode())
                self.assertIsNone(result["parsed"])
                self.assertEqual(result["parse_error_kind"], "unsafe_text")
        self.assertIn(
            "$/{object-value:0}/{object-value:0}/[1]",
            results["/api/v2/health"]["parse_error"],
        )
        recorder.export_jsonl().encode("utf-8")

    def test_decoded_json_rejects_unsafe_object_key_without_echoing_it(self) -> None:
        raw = b'{"\\ud800":"unsafe"}'
        with DeviceFixture({"/api/v2/info": raw}) as fixture:
            recorder = SessionRecorder()
            result = DragonClient(parse_target(fixture.target), recorder).fetch_json(
                "/api/v2/info"
            )

        self.assertEqual(result["raw_payload"], raw.decode())
        self.assertIsNone(result["parsed"])
        self.assertEqual(
            result["parse_error"],
            "parsed JSON contains non-UTF-8-encodable text at $/{object-key:0}",
        )
        self.assertEqual(result["parse_error_kind"], "unsafe_text")
        jsonl = recorder.export_jsonl()
        jsonl.encode("utf-8")
        self.assertIn('"raw_payload":"{\\"\\\\ud800\\":\\"unsafe\\"}"', jsonl)

    def test_valid_unicode_is_admitted_and_preserved_exactly(self) -> None:
        raw = (
            '{"emoji":"\\ud83d\\ude80","text":"日本語 café e\\u0301"}'
        ).encode()
        with DeviceFixture({"/api/v2/info": raw}) as fixture:
            recorder = SessionRecorder()
            result = DragonClient(parse_target(fixture.target), recorder).fetch_json(
                "/api/v2/info"
            )

        self.assertTrue(result["ok"])
        self.assertIsNone(result["parse_error_kind"])
        self.assertEqual(result["raw_payload"], raw.decode())
        self.assertEqual(result["parsed"], {"emoji": "🚀", "text": "日本語 café é"})
        exported = recorder.export_jsonl().encode("utf-8")
        self.assertIn("🚀".encode(), exported)
        self.assertIn("日本語 café é".encode(), exported)

    def test_malformed_and_oversized_responses_are_recorded_as_failures(self) -> None:
        with DeviceFixture({"/api/v2/state": b"not-json"}) as fixture:
            recorder = SessionRecorder()
            client = DragonClient(parse_target(fixture.target), recorder)
            malformed = client.fetch_json("/api/v2/state")
            client.max_response_bytes = 4
            oversized = client.fetch_json("/api/v2/health")

        self.assertFalse(malformed["ok"])
        self.assertEqual(malformed["parse_error_kind"], "syntax")
        self.assertFalse(oversized["ok"])
        self.assertIn("ResponseTooLargeError", oversized["error"])
        self.assertTrue(oversized["response_received"])
        self.assertTrue(oversized["http_ok"])
        self.assertTrue(oversized["response_too_large"])
        self.assertFalse(oversized["parsed_available"])
        self.assertEqual(client.budget.active, 0)

    def test_malformed_http_status_line_is_recorded_as_transport_failure(self) -> None:
        with DeviceFixture({"bad_status_once": {"/api/v2/health"}}) as fixture:
            recorder = SessionRecorder()
            client = DragonClient(parse_target(fixture.target), recorder)
            result = client.fetch_json("/api/v2/health")

        self.assertFalse(result["ok"])
        self.assertIsNone(result["status"])
        self.assertFalse(result["response_received"])
        self.assertFalse(result["http_ok"])
        self.assertIn("BadStatusLine", result["error"])
        error = next(
            record for record in recorder.snapshot() if record["kind"] == "http_error"
        )
        self.assertEqual(error["endpoint"], "/api/v2/health")
        self.assertIn("BadStatusLine", error["error"])
        self.assertEqual(client.budget.active, 0)

    def test_unavailable_endpoint_preserves_status_and_body(self) -> None:
        with DeviceFixture({"events_status": 503}) as fixture:
            recorder = SessionRecorder()
            client = DragonClient(parse_target(fixture.target), recorder)
            states: list[tuple[str, dict[str, object]]] = []
            client.stream_events(Event(), lambda event: None, lambda state, data: states.append((state, data)))

        unavailable = next(data for state, data in states if state == "unavailable")
        self.assertEqual(unavailable["status"], 503)
        self.assertIn('"future_detail":true', unavailable["raw_payload"])
        self.assertEqual(states[-1][1]["reason"], "unavailable")
        self.assertEqual(client.budget.active, 0)

    def test_json_http_errors_preserve_raw_and_parse_valid_bodies(self) -> None:
        raw = b'{"error":"missing","future_detail":{"value":true}}'
        config = {
            "/api/v2/info": raw,
            "/api/v2/state": b"not-json",
            "json_status": {"/api/v2/info": 404, "/api/v2/state": 503},
        }
        with DeviceFixture(config) as fixture:
            recorder = SessionRecorder()
            client = DragonClient(parse_target(fixture.target), recorder)
            valid = client.fetch_json("/api/v2/info")
            invalid = client.fetch_json("/api/v2/state")

        self.assertEqual(valid["raw_payload"], raw.decode())
        self.assertEqual(valid["parsed"], {"error": "missing", "future_detail": {"value": True}})
        self.assertIsNone(valid["parse_error"])
        self.assertIsNone(valid["parse_error_kind"])
        self.assertEqual(invalid["raw_payload"], "not-json")
        self.assertIsNone(invalid["parsed"])
        self.assertEqual(invalid["parse_error_kind"], "syntax")

    def test_http_and_sse_error_bodies_apply_unicode_admission_boundary(self) -> None:
        raw = b'{"error":"\\ud800"}'
        config = {
            "/api/v2/info": raw,
            "json_status": {"/api/v2/info": 503},
            "events_status": 503,
            "events_error_body": raw,
        }
        with DeviceFixture(config) as fixture:
            recorder = SessionRecorder()
            client = DragonClient(parse_target(fixture.target), recorder)
            http_result = client.fetch_json("/api/v2/info")
            states: list[tuple[str, dict[str, object]]] = []
            client.stream_events(
                Event(), lambda event: None, lambda state, data: states.append((state, data))
            )

        sse_result = next(data for state, data in states if state == "unavailable")
        for result in (http_result, sse_result):
            self.assertEqual(result["raw_payload"], raw.decode())
            self.assertIsNone(result["parsed"])
            self.assertEqual(result["parse_error_kind"], "unsafe_text")
        recorder.export_jsonl().encode("utf-8")

    def test_deep_json_is_iteratively_rejected_before_recursive_local_surfaces(self) -> None:
        depth = 900
        raw = ("[" * depth + '"ok"' + "]" * depth).encode()
        json.loads(raw)
        self.assertGreater(depth, MAX_PARSED_JSON_DEPTH)
        with DeviceFixture({"/api/v2/info": raw}) as fixture:
            recorder = SessionRecorder()
            result = DragonClient(parse_target(fixture.target), recorder).fetch_json(
                "/api/v2/info"
            )

        self.assertTrue(result["response_received"])
        self.assertTrue(result["http_ok"])
        self.assertFalse(result["ok"])
        self.assertFalse(result["parsed_available"])
        self.assertIsNone(result["parsed"])
        self.assertEqual(result["parse_error_kind"], "structure_too_deep")
        self.assertEqual(result["raw_payload"], raw.decode())
        recorder.export_jsonl().encode("utf-8")

    def test_json_deeper_than_decoder_limit_is_contained(self) -> None:
        depth = 10_000
        raw = ("[" * depth + "0" + "]" * depth).encode()
        with DeviceFixture({"/api/v2/info": raw}) as fixture:
            recorder = SessionRecorder()
            result = DragonClient(parse_target(fixture.target), recorder).fetch_json(
                "/api/v2/info"
            )

        self.assertTrue(result["response_received"])
        self.assertTrue(result["http_ok"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["parse_error_kind"], "structure_too_deep")
        self.assertEqual(result["parse_error"], "JSON nesting exceeds the decoder limit")
        self.assertEqual(result["raw_payload"], raw.decode())
        recorder.export_jsonl().encode("utf-8")

    def test_result_statuses_separate_reachability_http_and_structured_use(self) -> None:
        config = {
            "/api/v2/info": b'{"valid":true}',
            "/api/v2/state": b'{"unsafe":"\\ud800"}',
            "/api/v2/health": b'{"rejected":true}',
            "json_status": {"/api/v2/health": 503},
        }
        with DeviceFixture(config) as fixture:
            client = DragonClient(parse_target(fixture.target), SessionRecorder())
            valid = client.fetch_json("/api/v2/info")
            unsafe = client.fetch_json("/api/v2/state")
            rejected = client.fetch_json("/api/v2/health")
        failed = DragonClient(
            parse_target("127.0.0.1:1"), SessionRecorder(), request_timeout=0.1
        ).fetch_json("/api/v2/info")

        self.assertEqual(
            (valid["response_received"], valid["http_ok"], valid["ok"]),
            (True, True, True),
        )
        self.assertEqual(
            (unsafe["response_received"], unsafe["http_ok"], unsafe["ok"]),
            (True, True, False),
        )
        self.assertEqual(unsafe["parse_error_kind"], "unsafe_text")
        self.assertEqual(
            (rejected["response_received"], rejected["http_ok"], rejected["ok"]),
            (True, False, False),
        )
        self.assertTrue(rejected["parsed_available"])
        self.assertEqual(
            (failed["response_received"], failed["http_ok"], failed["ok"]),
            (False, False, False),
        )

    def test_invalid_http_utf8_is_marked_and_not_admitted_as_parsed_data(self) -> None:
        body = b'{"value":"\xff"}'
        with DeviceFixture({"/api/v2/info": body}) as fixture:
            recorder = SessionRecorder()
            result = DragonClient(parse_target(fixture.target), recorder).fetch_json(
                "/api/v2/info"
            )

        self.assertTrue(result["response_received"])
        self.assertTrue(result["http_ok"])
        self.assertFalse(result["ok"])
        self.assertFalse(result["parsed_available"])
        self.assertIn("0xff", result["decode_error"])
        self.assertEqual(result["raw_payload"], '{"value":"�"}')
        self.assertIsNone(result["parsed"])
        self.assertIsNone(result["parse_error"])
        self.assertIsNone(result["parse_error_kind"])
        recorder.export_jsonl().encode("utf-8")

    def test_oversized_http_error_body_has_separate_non_parse_classification(self) -> None:
        body = b'{"unsafe":"\\ud800"}' + b"x" * 20
        with DeviceFixture({
            "/api/v2/info": body,
            "json_status": {"/api/v2/info": 503},
        }) as fixture:
            recorder = SessionRecorder()
            client = DragonClient(
                parse_target(fixture.target), recorder, max_response_bytes=20
            )
            result = client.fetch_json("/api/v2/info")

        self.assertTrue(result["response_received"])
        self.assertFalse(result["http_ok"])
        self.assertTrue(result["response_too_large"])
        self.assertFalse(result["parsed_available"])
        self.assertEqual(len(result["raw_payload"].encode()), 20)
        self.assertIsNone(result["parsed"])
        self.assertIsNone(result["parse_error"])
        self.assertIsNone(result["parse_error_kind"])
        recorder.export_jsonl().encode("utf-8")

    def test_sse_lifecycle_parses_events_and_preserves_raw_blocks(self) -> None:
        with DeviceFixture() as fixture:
            recorder = SessionRecorder()
            client = DragonClient(parse_target(fixture.target), recorder)
            events: list[dict[str, object]] = []
            states: list[str] = []
            client.stream_events(Event(), events.append, lambda state, data: states.append(state))

        self.assertEqual(states, ["connecting", "open", "closed"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "telemetry")
        self.assertEqual(events[0]["event_id"], "0")
        self.assertEqual(events[0]["parsed"], {"known": 1, "unknown": 2})
        self.assertIsNone(events[0]["decode_error"])
        self.assertIsNone(events[0]["parse_error_kind"])
        self.assertEqual(events[0]["raw_payload"], 'event: telemetry\nid: 0\ndata: {"known":1,"unknown":2}\n\n')
        comment = next(record for record in recorder.snapshot() if record["kind"] == "sse_comment")
        self.assertEqual(comment["raw_payload"], ": connected\n\n")
        self.assertEqual(client.budget.active, 0)

    def test_sse_parsed_data_uses_the_same_unicode_admission_boundary(self) -> None:
        raw_data = '{"nested":["ok","\\ud800"]}'
        with DeviceFixture({"event_data": raw_data}) as fixture:
            recorder = SessionRecorder()
            client = DragonClient(parse_target(fixture.target), recorder)
            events: list[dict[str, object]] = []
            client.stream_events(Event(), events.append, lambda state, data: None)

        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["parsed"])
        self.assertEqual(events[0]["parse_error_kind"], "unsafe_text")
        self.assertIn(f"data: {raw_data}", events[0]["raw_payload"])
        recorder.export_jsonl().encode("utf-8")

    def test_sse_invalid_utf8_retains_first_decode_error_and_safe_text(self) -> None:
        blocks = [
            b'event: telemetry\ndata: {"first":"\xff"}\ndata: {"second":"\xfe"}\n\n'
        ]
        with DeviceFixture({"event_blocks": blocks}) as fixture:
            recorder = SessionRecorder()
            client = DragonClient(parse_target(fixture.target), recorder)
            events: list[dict[str, object]] = []
            client.stream_events(Event(), events.append, lambda state, data: None)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIn("0xff", event["decode_error"])
        self.assertNotIn("0xfe", event["decode_error"])
        self.assertIn('data: {"first":"�"}', event["raw_payload"])
        self.assertIn('data: {"second":"�"}', event["raw_payload"])
        self.assertFalse(event["parsed_available"])
        self.assertIsNone(event["parsed"])
        self.assertIsNone(event["parse_error_kind"])
        recorder.export_jsonl().encode("utf-8")

    def test_quiet_sse_stream_has_no_application_inactivity_timeout(self) -> None:
        with DeviceFixture({"quiet_seconds": 0.5}) as fixture:
            recorder = SessionRecorder()
            client = DragonClient(
                parse_target(fixture.target),
                recorder,
                request_timeout=0.05,
                sse_connect_timeout=0.05,
            )
            stop = Event()
            states: list[str] = []
            thread = Thread(
                target=client.stream_events,
                args=(stop, lambda event: None, lambda state, data: states.append(state)),
            )
            thread.start()
            deadline = time.monotonic() + 1
            while "open" not in states and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn("open", states)
            time.sleep(0.15)
            self.assertTrue(thread.is_alive())
            self.assertNotIn("error", states)
            stop.set()
            client.close_stream()
            thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(states[-1], "closed")
        self.assertEqual(client.budget.active, 0)

    def test_intentional_close_translates_blocked_reader_attribute_error_to_stop(self) -> None:
        response = BlockingTeardownResponse()
        recorder = SessionRecorder()
        client = DragonClient(
            parse_target("dragon.local"),
            recorder,
            opener=lambda request, timeout: response,
        )
        stop = Event()
        states: list[tuple[str, dict[str, object]]] = []
        escaped: list[BaseException] = []

        def run() -> None:
            try:
                client.stream_events(
                    stop,
                    lambda event: None,
                    lambda state, details: states.append((state, details)),
                )
            except BaseException as exc:
                escaped.append(exc)

        thread = Thread(target=run)
        thread.start()
        self.assertTrue(response.read_started.wait(timeout=1))
        stop.set()
        client.close_stream()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(escaped, [])
        self.assertEqual(states[-1], ("closed", {"request_id": 1, "endpoint": "/api/v2/events", "reason": "stopped"}))
        self.assertFalse(any(record["kind"] == "sse_error" for record in recorder.snapshot()))
        self.assertEqual(client.budget.active, 0)

    def test_reader_attribute_error_without_stop_is_recorded_as_failure(self) -> None:
        response = BlockingTeardownResponse()
        recorder = SessionRecorder()
        client = DragonClient(
            parse_target("dragon.local"),
            recorder,
            opener=lambda request, timeout: response,
        )
        states: list[tuple[str, dict[str, object]]] = []
        thread = Thread(
            target=client.stream_events,
            args=(Event(), lambda event: None, lambda state, details: states.append((state, details))),
        )
        thread.start()
        self.assertTrue(response.read_started.wait(timeout=1))
        response.close()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertIn("error", [state for state, details in states])
        error = next(record for record in recorder.snapshot() if record["kind"] == "sse_error")
        self.assertIn("AttributeError", error["error"])
        self.assertEqual(states[-1][1]["reason"], "error")
        self.assertEqual(client.budget.active, 0)

    def test_incomplete_read_is_recorded_as_error_without_escaping(self) -> None:
        recorder = SessionRecorder()
        client = DragonClient(
            parse_target("dragon.local"),
            recorder,
            opener=lambda request, timeout: IncompleteReadResponse(),
        )
        states: list[tuple[str, dict[str, object]]] = []

        client.stream_events(
            Event(),
            lambda event: None,
            lambda state, details: states.append((state, details)),
        )

        error = next(record for record in recorder.snapshot() if record["kind"] == "sse_error")
        self.assertIn("IncompleteRead", error["error"])
        self.assertEqual(states[-1][1]["reason"], "error")
        self.assertFalse(any(
            record["kind"] == "sse_closed" and record["reason"] == "stopped"
            for record in recorder.snapshot()
        ))
        self.assertEqual(client.budget.active, 0)

    def test_connection_budget_reports_active_use_and_releases(self) -> None:
        budget = ConnectionBudget(2, acquire_timeout=0.01)
        self.assertEqual(budget.active, 0)
        with budget.lease():
            self.assertEqual(budget.active, 1)
            with budget.lease():
                self.assertEqual(budget.active, 2)
        self.assertEqual(budget.active, 0)

    def test_connection_budget_rejects_work_beyond_the_limit(self) -> None:
        budget = ConnectionBudget(1, acquire_timeout=0.01)
        with budget.lease():
            with self.assertRaises(TimeoutError):
                with budget.lease():
                    pass
        self.assertEqual(budget.active, 0)

    def test_only_read_only_fixed_endpoints_are_accepted(self) -> None:
        recorder = SessionRecorder()
        client = DragonClient(parse_target("dragon.local"), recorder)
        with self.assertRaises(ValueError):
            client.fetch_json("/api/v2/settings")
