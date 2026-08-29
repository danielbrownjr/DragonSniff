from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import IncompleteRead
from threading import Event, Thread
import time
from unittest import TestCase

from dragonsniff.client import ConnectionBudget, DragonClient
from dragonsniff.recording import SessionRecorder
from dragonsniff.target import parse_target


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        if self.path == "/api/v2/events":
            if config.get("events_connect_delay"):
                time.sleep(config["events_connect_delay"])
            if config.get("events_status", 200) != 200:
                body = b'{"error":"busy","future_detail":true}'
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
            self.wfile.write(b": connected\n\n")
            self.wfile.write(b"event: telemetry\nid: abc\ndata: {\"known\":1,\"unknown\":2}\n\n")
            self.wfile.flush()
            return
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

    def test_malformed_and_oversized_responses_are_recorded_as_failures(self) -> None:
        with DeviceFixture({"/api/v2/state": b"not-json"}) as fixture:
            recorder = SessionRecorder()
            client = DragonClient(parse_target(fixture.target), recorder)
            malformed = client.fetch_json("/api/v2/state")
            client.max_response_bytes = 4
            oversized = client.fetch_json("/api/v2/health")

        self.assertFalse(malformed["ok"])
        self.assertIsNotNone(malformed["parse_error"])
        self.assertFalse(oversized["ok"])
        self.assertIn("ResponseTooLargeError", oversized["error"])
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
        self.assertEqual(invalid["raw_payload"], "not-json")
        self.assertIsNone(invalid["parsed"])
        self.assertIsNotNone(invalid["parse_error"])

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
        self.assertEqual(events[0]["event_id"], "abc")
        self.assertEqual(events[0]["parsed"], {"known": 1, "unknown": 2})
        self.assertEqual(events[0]["raw_payload"], 'event: telemetry\nid: abc\ndata: {"known":1,"unknown":2}\n\n')
        comment = next(record for record in recorder.snapshot() if record["kind"] == "sse_comment")
        self.assertEqual(comment["raw_payload"], ": connected\n\n")
        self.assertEqual(client.budget.active, 0)

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
