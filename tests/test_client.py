from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
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
            self.wfile.write(b": connected\n\n")
            self.wfile.write(b"event: telemetry\nid: abc\ndata: {\"known\":1,\"unknown\":2}\n\n")
            self.wfile.flush()
            return
        body = config.get(self.path, b'{"api_version":2}')
        self.send_response(200)
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

    def test_sse_lifecycle_parses_events_and_preserves_raw_blocks(self) -> None:
        with DeviceFixture() as fixture:
            recorder = SessionRecorder()
            client = DragonClient(parse_target(fixture.target), recorder)
            events: list[dict[str, object]] = []
            states: list[str] = []
            client.stream_events(Event(), events.append, lambda state, data: states.append(state))

        self.assertEqual(states, ["connecting", "open", "closed"])
        self.assertEqual(events[1]["event"], "telemetry")
        self.assertEqual(events[1]["event_id"], "abc")
        self.assertEqual(events[1]["parsed"], {"known": 1, "unknown": 2})
        self.assertEqual(events[1]["raw_payload"], 'event: telemetry\nid: abc\ndata: {"known":1,"unknown":2}\n\n')
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
