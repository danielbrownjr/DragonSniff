import threading
import time
from typing import Callable
from unittest import TestCase

from dragonsniff.capture import CaptureConfig, CaptureRunner
from dragonsniff.client import DragonClient
from dragonsniff.recording import SessionRecorder
from dragonsniff.target import parse_target

from tests.test_client import DeviceFixture


def wait_until(predicate: Callable[[], bool], timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


def short_config() -> CaptureConfig:
    return CaptureConfig(
        duration_seconds=1.0,
        state_interval_seconds=0.5,
        health_interval_seconds=5.0,
    )


def capture_threads() -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("dragonsniff-capture")
    ]


class CaptureConfigTests(TestCase):
    def test_named_profiles_are_exact_bounded_schedules(self) -> None:
        self.assertEqual(
            CaptureConfig.profiles(),
            {
                "Smoke": CaptureConfig(120.0, 1.0, 10.0),
                "Soak": CaptureConfig(900.0, 2.0, 30.0),
                "Extended": CaptureConfig(1_800.0, 5.0, 60.0),
                "Long Haul": CaptureConfig(28_800.0, 5.0, 60.0),
            },
        )
        for config in CaptureConfig.profiles().values():
            config.validate()
            self.assertLessEqual(
                config.estimated_records(), CaptureConfig.MAX_ESTIMATED_RECORDS
            )

    def test_profile_name_tracks_exact_profile_and_custom_edits(self) -> None:
        self.assertEqual(CaptureConfig().profile_name(), "Smoke")
        self.assertEqual(CaptureConfig(900.0, 2.0, 30.0).profile_name(), "Soak")
        self.assertEqual(
            CaptureConfig(28_800.0, 5.0, 60.0).profile_name(), "Long Haul"
        )
        self.assertEqual(CaptureConfig(600.0, 2.0, 30.0).profile_name(), "Custom")

    def test_rejects_invalid_unknown_and_record_overflow_schedules(self) -> None:
        invalid = (
            {"duration_seconds": 0},
            {"state_interval_seconds": 0.1},
            {"health_interval_seconds": 1},
            {"duration_seconds": True},
            {"sse": True},
            {
                "duration_seconds": 43_200,
                "state_interval_seconds": 0.5,
                "health_interval_seconds": 5,
            },
            {"duration_seconds": 43_201},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                CaptureConfig.from_value(value)

    def test_long_haul_runner_sizes_recorder_to_retain_complete_capture(self) -> None:
        config = CaptureConfig.profiles()["Long Haul"]
        runner = CaptureRunner(parse_target("dragon.local"), config)

        self.assertEqual(runner.recorder.max_records, config.estimated_records())
        self.assertGreater(config.estimated_records(), 2_000)
        self.assertLessEqual(
            config.estimated_records(), CaptureConfig.MAX_ESTIMATED_RECORDS
        )

    def test_rejects_explicit_recorder_smaller_than_capture_schedule(self) -> None:
        config = CaptureConfig.profiles()["Long Haul"]

        with self.assertRaisesRegex(ValueError, "recorder is smaller"):
            CaptureRunner(
                parse_target("dragon.local"),
                config,
                recorder=SessionRecorder(max_records=2_000),
            )


class CaptureRunnerTests(TestCase):
    def assert_clean(self, runner: CaptureRunner) -> None:
        self.assertEqual(runner.client.budget.active, 0)
        self.assertEqual(capture_threads(), [])
        self.assertTrue(runner.snapshot()["cleanup_complete"])

    def test_capture_polls_only_fixed_json_endpoints_and_preserves_raw_evidence(self) -> None:
        state = b'{"chamber_temp":44.2,"heater":{"duty":0.35},"future":true}'
        info = b'{"product":"DragonBreath","version":"pid-rc1"}'
        health_sequence = [
            b'{"boot_id":"boot-a","free_heap":1234}',
            b'{"boot_id":"boot-a","free_heap":1200}',
        ]
        with DeviceFixture({
            "/api/v2/info": info,
            "/api/v2/state": state,
            "health_sequence": health_sequence,
        }) as fixture:
            runner = CaptureRunner(parse_target(fixture.target), short_config())
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")
            snapshot = runner.snapshot()

        self.assertGreaterEqual(snapshot["samples_completed"], 2)
        self.assertEqual(snapshot["state_failures"], 0)
        self.assertEqual(snapshot["health_failures"], 0)
        self.assertEqual(snapshot["initial_boot_id"], "boot-a")
        self.assertFalse(snapshot["boot_id_changed"])
        self.assertEqual(snapshot["latest_info"]["parsed"]["version"], "pid-rc1")
        self.assertIn('"future":true', snapshot["latest_state"]["raw_payload"])
        records = runner.recorder.snapshot()
        requests = [record for record in records if record["kind"] == "http_request"]
        self.assertTrue(requests)
        self.assertEqual({record["method"] for record in requests}, {"GET"})
        self.assertEqual(
            {record["endpoint"] for record in requests},
            {"/api/v2/info", "/api/v2/state", "/api/v2/health"},
        )
        self.assertEqual(
            [record["fetch_sequence"] for record in requests],
            list(range(1, len(requests) + 1)),
        )
        self.assertTrue(all("sample" not in record for record in requests))
        self.assertEqual(snapshot["fetches_completed"], len(requests))
        terminal = next(
            record for record in records if record["kind"] == "capture_run_completed"
        )
        self.assertEqual(terminal["samples_completed"], snapshot["samples_completed"])
        self.assertEqual(terminal["fetches_completed"], snapshot["fetches_completed"])
        self.assertFalse(any(record["kind"].startswith("sse_") for record in records))
        self.assert_clean(runner)

    def test_boot_change_is_evidence_without_an_invented_cause(self) -> None:
        health_sequence = [b'{"boot_id":"boot-a"}', b'{"boot_id":"boot-b"}']
        with DeviceFixture({"health_sequence": health_sequence}) as fixture:
            runner = CaptureRunner(parse_target(fixture.target), short_config())
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")
            snapshot = runner.snapshot()

        self.assertTrue(snapshot["boot_id_changed"])
        self.assertEqual(snapshot["boot_id_changes"][0]["from"], "boot-a")
        self.assertEqual(snapshot["boot_id_changes"][0]["to"], "boot-b")
        self.assertIsInstance(
            snapshot["boot_id_changes"][0]["fetch_sequence"], int
        )
        self.assertNotIn("sample", snapshot["boot_id_changes"][0])
        self.assertNotIn("cause", snapshot["boot_id_changes"][0])
        self.assert_clean(runner)

    def test_stop_cancels_future_samples_and_releases_resources(self) -> None:
        with DeviceFixture() as fixture:
            runner = CaptureRunner(parse_target(fixture.target), CaptureConfig())
            runner.start()
            wait_until(lambda: runner.snapshot()["samples_completed"] >= 1)
            self.assertTrue(runner.stop(timeout=1.0))
            snapshot = runner.snapshot()

        self.assertEqual(snapshot["state"], "cancelled")
        self.assertTrue(any(
            record["kind"] == "capture_run_cancelled"
            for record in runner.recorder.snapshot()
        ))
        self.assert_clean(runner)

    def test_stop_during_inflight_read_stays_stopping_until_cleanup(self) -> None:
        config = {
            "json_delay_seconds": {"/api/v2/info": 0.5},
        }
        with DeviceFixture(config) as fixture:
            runner = CaptureRunner(parse_target(fixture.target), short_config())
            runner.start()
            wait_until(lambda: runner.client.budget.active == 1)
            self.assertFalse(runner.stop(timeout=0.01))
            self.assertEqual(runner.snapshot()["state"], "stopping")
            wait_until(lambda: runner.snapshot()["state"] == "cancelled")

        self.assert_clean(runner)

    def test_internal_failure_is_recorded_and_worker_cleans_up(self) -> None:
        target = parse_target("dragon.local")
        recorder = SessionRecorder()

        class BrokenClient(DragonClient):
            def fetch_json(self, path, *, context=None):
                raise RuntimeError("controlled capture failure")

        runner = CaptureRunner(
            target,
            short_config(),
            client=BrokenClient(target, recorder, connection_limit=1),
        )
        runner.start()
        wait_until(lambda: runner.snapshot()["state"] == "failed")
        snapshot = runner.snapshot()

        self.assertEqual(snapshot["failure"]["type"], "RuntimeError")
        self.assertTrue(any(
            record["kind"] == "capture_internal_failure"
            for record in runner.recorder.snapshot()
        ))
        self.assert_clean(runner)
