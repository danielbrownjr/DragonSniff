from http.client import HTTPConnection
import json
from threading import Thread
import time
from unittest import TestCase

from dragonsniff.server import DragonSniffServer

from tests.test_client import DeviceFixture


class LocalServerFixture:
    def __init__(self) -> None:
        self.server = DragonSniffServer(("127.0.0.1", 0))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "LocalServerFixture":
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method: str, path: str, body: object | None = None) -> tuple[int, bytes, str]:
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        encoded = None if body is None else json.dumps(body).encode()
        headers = {} if encoded is None else {"Content-Type": "application/json"}
        connection.request(method, path, encoded, headers)
        response = connection.getresponse()
        result = (response.status, response.read(), response.getheader("Content-Type"))
        connection.close()
        return result


class ServerTests(TestCase):
    def test_server_refuses_non_loopback_bind(self) -> None:
        with self.assertRaises(ValueError):
            DragonSniffServer(("0.0.0.0", 0))

    def test_static_ui_and_idle_session_are_available_locally(self) -> None:
        with LocalServerFixture() as local:
            status, body, content_type = local.request("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn(b"DragonSniff", body)
            self.assertEqual(content_type, "text/html; charset=utf-8")
            status, body, _ = local.request("GET", "/local/v1/session")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["session_state"], "idle")

    def test_local_api_starts_observation_and_exports_jsonl(self) -> None:
        with DeviceFixture() as device, LocalServerFixture() as local:
            status, _, _ = local.request(
                "POST", "/local/v1/session/start", {"target": device.target}
            )
            self.assertEqual(status, 202)
            deadline = time.monotonic() + 2
            snapshot = {}
            while time.monotonic() < deadline:
                _, body, _ = local.request("GET", "/local/v1/session")
                snapshot = json.loads(body)
                if snapshot["sse"]["state"] == "closed":
                    break
                time.sleep(0.01)
            self.assertEqual(snapshot["http"]["/api/v2/info"]["state"], "available")
            self.assertEqual(snapshot["sse"]["events"], 2)
            status, export, content_type = local.request("GET", "/local/v1/session/export")
            self.assertEqual(status, 200)
            self.assertEqual(content_type, "application/x-ndjson; charset=utf-8")
            records = [json.loads(line) for line in export.splitlines()]
            self.assertTrue(any(record["kind"] == "sse_event" for record in records))

    def test_static_ui_explains_direct_file_use_and_exposes_copy_controls(self) -> None:
        with LocalServerFixture() as local:
            status, body, _ = local.request("GET", "/")
        html = body.decode()
        self.assertEqual(status, 200)
        self.assertIn("DragonSniff is not a standalone HTML file", html)
        self.assertEqual(html.count('data-copy-view="parsed"'), 3)
        self.assertEqual(html.count('data-copy-view="raw"'), 3)
        self.assertIn("Stop event stream", html)

    def test_invalid_target_is_rejected_without_starting_a_session(self) -> None:
        with LocalServerFixture() as local:
            status, body, _ = local.request(
                "POST", "/local/v1/session/start", {"target": "dragon.local/api/settings"}
            )
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(body)["error"], "invalid_request")
            _, body, _ = local.request("GET", "/local/v1/session")
            self.assertEqual(json.loads(body)["session_state"], "idle")
