from http.client import HTTPConnection
import json
from threading import Thread
import time
from unittest import TestCase

from dragonsniff.server import DragonSniffServer, SessionManager

from tests.test_client import DeviceFixture


class LocalServerFixture:
    def __init__(self, manager: SessionManager | None = None) -> None:
        self.server = DragonSniffServer(("127.0.0.1", 0), manager)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "LocalServerFixture":
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        method: str,
        path: str,
        body: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, str]:
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        encoded = None if body is None else json.dumps(body).encode()
        request_headers = {} if encoded is None else {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        connection.request(method, path, encoded, request_headers)
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

    def test_family_ui_favicon_and_unlinked_lab_route_are_served(self) -> None:
        with LocalServerFixture() as local:
            lab_status, lab_body, lab_type = local.request("GET", "/lab")
            svg_status, svg_body, svg_type = local.request("GET", "/favicon.svg")
            ico_status, ico_body, ico_type = local.request("GET", "/favicon.ico")

        self.assertEqual((lab_status, lab_type), (200, "text/html; charset=utf-8"))
        self.assertIn(b"Super Secret Squirrel Laboratory", lab_body)
        self.assertNotIn(b'data-page="lab"', lab_body)
        self.assertEqual((svg_status, svg_type), (200, "image/svg+xml"))
        self.assertIn(b"prefers-color-scheme", svg_body)
        self.assertEqual((ico_status, ico_type), (200, "image/png"))
        self.assertTrue(ico_body.startswith(b"\x89PNG\r\n\x1a\n"))

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
            self.assertEqual(snapshot["sse"]["events"], 1)
            status, export, content_type = local.request("GET", "/local/v1/session/export")
            self.assertEqual(status, 200)
            self.assertEqual(content_type, "application/x-ndjson; charset=utf-8")
            records = [json.loads(line) for line in export.splitlines()]
            self.assertTrue(any(record["kind"] == "sse_event" for record in records))

    def test_static_ui_explains_direct_file_use_and_exposes_copy_controls(self) -> None:
        with LocalServerFixture() as local:
            status, body, _ = local.request("GET", "/")
            app_status, app_body, _ = local.request("GET", "/app.js")
        html = body.decode()
        app = app_body.decode()
        self.assertEqual(status, 200)
        self.assertEqual(app_status, 200)
        self.assertIn("DragonSniff is not a standalone HTML file", html)
        self.assertEqual(html.count('data-copy-view="parsed"'), 3)
        self.assertEqual(html.count('data-copy-view="raw"'), 3)
        self.assertIn("Stop event stream", html)
        self.assertIn("Poke it with a stick", html)
        self.assertIn("Watch the dragon breathe", html)
        self.assertIn("Start passive capture", html)
        self.assertIn('id="captureProfile"', html)
        self.assertIn('id="thermal-heading">Thermals', html)
        self.assertIn('id="pidGauge"', html)
        self.assertIn('id="pidGaugeNeedle"', html)
        self.assertIn('data-page="thermal"', html)
        self.assertIn('data-page="churn"', html)
        self.assertIn('href="/favicon.svg"', html)
        self.assertIn('id="labPollInterval"', html)
        self.assertIn("Start bounded churn", html)
        self.assertIn('id="churnDelaySeconds"', html)
        self.assertIn('step="0.05"', html)
        self.assertIn('id="churnSettlement"', html)
        self.assertIn('id="churnProfile"', html)
        self.assertIn("Choose a repeatable bounded profile", html)
        self.assertIn('id="churnDelaySeconds" type="number" value="0.5" min="0.1" max="5" step="0.05"', html)
        self.assertIn("Copy run summary", html)
        self.assertIn("Starting a capture pauses an active live session", html)
        self.assertIn("Starting churn pauses an active live session", html)
        self.assertIn("async function startAutomatedTest", app)
        self.assertIn('navigateToPage("dashboard")', app)

    def test_idle_snapshot_exposes_named_churn_profiles(self) -> None:
        with LocalServerFixture() as local:
            status, body, _ = local.request("GET", "/local/v1/session")

        churn = json.loads(body)["churn"]
        self.assertEqual(status, 200)
        self.assertEqual(churn["profile"], "Baseline")
        self.assertEqual(churn["profiles"]["Baseline"], {
            "cycles": 3, "observe_seconds": 2.0, "max_events": 3, "delay_seconds": 0.5,
        })
        self.assertEqual(churn["profiles"]["Extended"], {
            "cycles": 10, "observe_seconds": 5.0, "max_events": 5, "delay_seconds": 0.25,
        })
        self.assertEqual(churn["profiles"]["Stress"], {
            "cycles": 20, "observe_seconds": 10.0, "max_events": 10, "delay_seconds": 0.1,
        })

    def test_idle_snapshot_exposes_named_capture_profiles(self) -> None:
        with LocalServerFixture() as local:
            status, body, _ = local.request("GET", "/local/v1/session")

        capture = json.loads(body)["capture"]
        self.assertEqual(status, 200)
        self.assertEqual(capture["profile"], "Smoke")
        self.assertEqual(capture["profiles"]["Smoke"], {
            "duration_seconds": 120.0,
            "state_interval_seconds": 1.0,
            "health_interval_seconds": 10.0,
        })
        self.assertEqual(capture["profiles"]["Soak"]["duration_seconds"], 900.0)
        self.assertEqual(capture["profiles"]["Extended"]["duration_seconds"], 1800.0)
        self.assertEqual(capture["profiles"]["Long Haul"], {
            "duration_seconds": 28_800.0,
            "state_interval_seconds": 5.0,
            "health_interval_seconds": 60.0,
        })

    def test_invalid_target_is_rejected_without_starting_a_session(self) -> None:
        with LocalServerFixture() as local:
            status, body, _ = local.request(
                "POST", "/local/v1/session/start", {"target": "dragon.local/api/settings"}
            )
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(body)["error"], "invalid_request")
            _, body, _ = local.request("GET", "/local/v1/session")
            self.assertEqual(json.loads(body)["session_state"], "idle")

    def test_unexpected_host_is_rejected(self) -> None:
        with LocalServerFixture() as local:
            status, body, _ = local.request(
                "GET", "/local/v1/session", headers={"Host": "attacker.example:8765"}
            )

        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "forbidden")

    def test_post_origin_must_match_the_local_service_origin(self) -> None:
        with LocalServerFixture() as local:
            expected_origin = f"http://127.0.0.1:{local.server.server_port}"
            accepted, _, _ = local.request(
                "POST",
                "/local/v1/session/start",
                {"target": "127.0.0.1:9"},
                {"Origin": expected_origin},
            )
            rejected, body, _ = local.request(
                "POST",
                "/local/v1/session/stop",
                {},
                {"Origin": "http://attacker.example"},
            )

        self.assertEqual(accepted, 202)
        self.assertEqual(rejected, 403)
        self.assertEqual(json.loads(body)["error"], "forbidden")

    def test_post_without_origin_is_allowed_for_non_browser_tooling(self) -> None:
        with LocalServerFixture() as local:
            status, _, _ = local.request(
                "POST", "/local/v1/session/start", {"target": "127.0.0.1:9"}
            )

        self.assertEqual(status, 202)

    def test_text_plain_post_is_rejected(self) -> None:
        with LocalServerFixture() as local:
            status, body, _ = local.request(
                "POST",
                "/local/v1/session/start",
                {"target": "127.0.0.1:9"},
                {"Content-Type": "text/plain"},
            )

        self.assertEqual(status, 415)
        self.assertEqual(json.loads(body)["error"], "unsupported_media_type")

    def test_new_session_does_not_replace_an_incompletely_stopped_session(self) -> None:
        manager = SessionManager()
        previous = type("StoppingObserver", (), {"stop": lambda self: False})()
        manager._observer = previous  # type: ignore[assignment]

        with self.assertRaisesRegex(RuntimeError, "still stopping"):
            manager.start("dragon.local")

        self.assertIs(manager.current(), previous)

    def test_incomplete_stop_returns_accepted_with_stopping_state(self) -> None:
        manager = SessionManager()

        class StoppingObserver:
            def stop(self, timeout: float = 2.0) -> bool:
                return timeout >= 6.0

            def snapshot(self) -> dict[str, object]:
                return {"session_state": "stopping"}

        manager._observer = StoppingObserver()  # type: ignore[assignment]
        with LocalServerFixture(manager) as local:
            status, body, _ = local.request("POST", "/local/v1/session/stop", {})

        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body)["session_state"], "stopping")

    def test_local_api_runs_bounded_churn_and_exports_same_jsonl_evidence(self) -> None:
        with DeviceFixture({"quiet_seconds": 0.8}) as device, LocalServerFixture() as local:
            status, body, _ = local.request(
                "POST",
                "/local/v1/churn/start",
                {
                    "target": device.target,
                    "configuration": {
                        "cycles": 1,
                        "observe_seconds": 0.25,
                        "max_events": 5,
                        "delay_seconds": 0.1,
                    },
                },
            )
            self.assertEqual(status, 202)
            self.assertEqual(json.loads(body)["churn"]["state"], "running")
            deadline = time.monotonic() + 4
            snapshot = {}
            while time.monotonic() < deadline:
                _, body, _ = local.request("GET", "/local/v1/session")
                snapshot = json.loads(body)
                if snapshot["churn"]["state"] == "completed":
                    break
                time.sleep(0.01)
            status, export, content_type = local.request(
                "GET", "/local/v1/session/export"
            )

        self.assertEqual(snapshot["active_mode"], "churn")
        self.assertEqual(snapshot["churn"]["successful_connections"], 1)
        self.assertTrue(snapshot["churn"]["cleanup_complete"])
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/x-ndjson; charset=utf-8")
        records = [json.loads(line) for line in export.splitlines()]
        self.assertTrue(any(record["kind"] == "churn_run_completed" for record in records))

    def test_capture_pauses_and_restores_observation_without_losing_evidence(self) -> None:
        with (
            DeviceFixture({"/api/v2/state": b'{"chamber_temp":42.5}'}) as device,
            LocalServerFixture() as local,
        ):
            observation_status, _, _ = local.request(
                "POST", "/local/v1/session/start", {"target": device.target}
            )
            status, body, _ = local.request(
                "POST",
                "/local/v1/capture/start",
                {
                    "target": device.target,
                    "configuration": {
                        "duration_seconds": 1,
                        "state_interval_seconds": 0.5,
                        "health_interval_seconds": 5,
                    },
                },
            )
            self.assertEqual(status, 202)
            self.assertEqual(json.loads(body)["capture"]["state"], "running")
            deadline = time.monotonic() + 3
            snapshot = {}
            while time.monotonic() < deadline:
                _, body, _ = local.request("GET", "/local/v1/session")
                snapshot = json.loads(body)
                if (
                    snapshot["active_mode"] == "observation"
                    and snapshot["capture"]["state"] == "completed"
                ):
                    break
                time.sleep(0.01)
            status, export, content_type = local.request(
                "GET", "/local/v1/session/export"
            )

        self.assertEqual(observation_status, 202)
        self.assertEqual(snapshot["active_mode"], "observation")
        self.assertIn(snapshot["session_state"], {"starting", "observing"})
        self.assertGreaterEqual(snapshot["capture"]["samples_completed"], 2)
        self.assertTrue(snapshot["capture"]["cleanup_complete"])
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/x-ndjson; charset=utf-8")
        records = [json.loads(line) for line in export.splitlines()]
        self.assertTrue(any(record["kind"] == "capture_run_completed" for record in records))
        self.assertFalse(any(record["kind"].startswith("sse_") for record in records))

    def test_capture_configuration_is_validated_server_side(self) -> None:
        with LocalServerFixture() as local:
            invalid_values = (
                {"duration_seconds": 0},
                {"state_interval_seconds": 0.1},
                {"health_interval_seconds": 1},
                {"concurrency": 2},
                {
                    "duration_seconds": 43_200,
                    "state_interval_seconds": 0.5,
                    "health_interval_seconds": 5,
                },
                {"duration_seconds": 43_201},
            )
            for configuration in invalid_values:
                status, body, _ = local.request(
                    "POST",
                    "/local/v1/capture/start",
                    {"target": "127.0.0.1:9", "configuration": configuration},
                )
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(body)["error"], "invalid_request")

    def test_capture_actions_keep_host_origin_and_content_type_boundary(self) -> None:
        payload = {
            "target": "127.0.0.1:9",
            "configuration": {
                "duration_seconds": 1,
                "state_interval_seconds": 0.5,
                "health_interval_seconds": 5,
            },
        }
        with LocalServerFixture() as local:
            port = local.server.server_port
            bad_host, host_body, _ = local.request(
                "POST",
                "/local/v1/capture/start",
                payload,
                {"Host": "attacker.example"},
            )
            bad_origin, origin_body, _ = local.request(
                "POST",
                "/local/v1/capture/start",
                payload,
                {"Origin": "http://attacker.example"},
            )
            bad_type, type_body, _ = local.request(
                "POST",
                "/local/v1/capture/start",
                payload,
                {
                    "Origin": f"http://127.0.0.1:{port}",
                    "Content-Type": "text/plain",
                },
            )

        self.assertEqual(bad_host, 403)
        self.assertEqual(json.loads(host_body)["error"], "forbidden")
        self.assertEqual(bad_origin, 403)
        self.assertEqual(json.loads(origin_body)["error"], "forbidden")
        self.assertEqual(bad_type, 415)
        self.assertEqual(json.loads(type_body)["error"], "unsupported_media_type")

    def test_churn_configuration_is_validated_server_side(self) -> None:
        with LocalServerFixture() as local:
            invalid_values = (
                {"cycles": 0},
                {"cycles": 21},
                {"observe_seconds": 0},
                {"max_events": 0},
                {"delay_seconds": 0},
                {"concurrency": 2},
            )
            for configuration in invalid_values:
                status, body, _ = local.request(
                    "POST",
                    "/local/v1/churn/start",
                    {"target": "127.0.0.1:9", "configuration": configuration},
                )
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(body)["error"], "invalid_request")

    def test_churn_actions_keep_host_origin_and_content_type_boundary(self) -> None:
        with LocalServerFixture() as local:
            port = local.server.server_port
            payload = {
                "target": "127.0.0.1:9",
                "configuration": {
                    "cycles": 1,
                    "observe_seconds": 0.25,
                    "max_events": 1,
                    "delay_seconds": 0.1,
                },
            }
            bad_host, host_body, _ = local.request(
                "POST",
                "/local/v1/churn/start",
                payload,
                {"Host": "attacker.example"},
            )
            bad_origin, origin_body, _ = local.request(
                "POST",
                "/local/v1/churn/start",
                payload,
                {"Origin": "http://attacker.example"},
            )
            bad_type, type_body, _ = local.request(
                "POST",
                "/local/v1/churn/start",
                payload,
                {
                    "Origin": f"http://127.0.0.1:{port}",
                    "Content-Type": "text/plain",
                },
            )

        self.assertEqual(bad_host, 403)
        self.assertEqual(json.loads(host_body)["error"], "forbidden")
        self.assertEqual(bad_origin, 403)
        self.assertEqual(json.loads(origin_body)["error"], "forbidden")
        self.assertEqual(bad_type, 415)
        self.assertEqual(json.loads(type_body)["error"], "unsupported_media_type")

    def test_churn_pauses_and_restores_normal_observation(self) -> None:
        with DeviceFixture({"quiet_seconds": 1.0}) as device, LocalServerFixture() as local:
            started, _, _ = local.request(
                "POST", "/local/v1/session/start", {"target": device.target}
            )
            churn_started, body, _ = local.request(
                "POST",
                "/local/v1/churn/start",
                {
                    "target": device.target,
                    "configuration": {
                        "cycles": 1,
                        "observe_seconds": 0.25,
                        "max_events": 1,
                        "delay_seconds": 0.1,
                    },
                },
            )
            started_snapshot = json.loads(body)
            deadline = time.monotonic() + 4
            snapshot = {}
            while time.monotonic() < deadline:
                _, body, _ = local.request("GET", "/local/v1/session")
                snapshot = json.loads(body)
                if (
                    snapshot["active_mode"] == "observation"
                    and snapshot["churn"]["state"]
                    in {"completed", "cancelled", "failed"}
                ):
                    break
                time.sleep(0.01)

        self.assertEqual(started, 202)
        self.assertEqual(churn_started, 202)
        self.assertEqual(started_snapshot["active_mode"], "churn")
        self.assertEqual(started_snapshot["session_state"], "idle")
        self.assertEqual(snapshot["active_mode"], "observation")
        self.assertEqual(snapshot["churn"]["state"], "completed")

    def test_automated_runs_remain_mutually_exclusive(self) -> None:
        with DeviceFixture({"quiet_seconds": 1.0}) as device, LocalServerFixture() as local:
            capture_payload = {
                "target": device.target,
                "configuration": {
                    "duration_seconds": 1,
                    "state_interval_seconds": 0.5,
                    "health_interval_seconds": 5,
                },
            }
            started, _, _ = local.request(
                "POST", "/local/v1/capture/start", capture_payload
            )
            observation_status, observation_body, _ = local.request(
                "POST", "/local/v1/session/start", {"target": device.target}
            )
            churn_status, churn_body, _ = local.request(
                "POST",
                "/local/v1/churn/start",
                {
                    "target": device.target,
                    "configuration": {
                        "cycles": 1,
                        "observe_seconds": 0.25,
                        "max_events": 1,
                        "delay_seconds": 0.1,
                    },
                },
            )

        self.assertEqual(started, 202)
        self.assertEqual(observation_status, 400)
        self.assertIn("capture run is active", json.loads(observation_body)["message"])
        self.assertEqual(churn_status, 400)
        self.assertIn("capture run is active", json.loads(churn_body)["message"])
