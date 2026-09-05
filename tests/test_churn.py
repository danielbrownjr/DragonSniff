import json
import threading
import time
from typing import Callable
from unittest import TestCase

from dragonsniff.churn import ChurnConfig, ChurnRunner
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


def short_config(*, cycles: int = 1, delay: float = 0.1) -> ChurnConfig:
    return ChurnConfig(
        cycles=cycles,
        observe_seconds=0.25,
        max_events=5,
        delay_seconds=delay,
    )


def churn_threads() -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("dragonsniff-churn")
    ]


class ChurnConfigTests(TestCase):
    def test_named_profiles_have_exact_values_and_remain_within_hard_bounds(self) -> None:
        profiles = ChurnConfig.profiles()

        self.assertEqual(
            profiles,
            {
                "Baseline": ChurnConfig(3, 2.0, 3, 0.5),
                "Extended": ChurnConfig(10, 5.0, 5, 0.25),
                "Stress": ChurnConfig(20, 10.0, 10, 0.1),
            },
        )
        for config in profiles.values():
            config.validate()

    def test_profile_name_tracks_exact_profiles_and_custom_edits(self) -> None:
        self.assertEqual(ChurnConfig().profile_name(), "Baseline")
        self.assertEqual(ChurnConfig(10, 5.0, 5, 0.25).profile_name(), "Extended")
        self.assertEqual(ChurnConfig(20, 10.0, 10, 0.1).profile_name(), "Stress")
        self.assertEqual(ChurnConfig(4, 2.0, 3, 0.5).profile_name(), "Custom")

    def test_defaults_are_conservative_and_all_fields_are_bounded(self) -> None:
        config = ChurnConfig.from_value({})

        self.assertEqual(config, ChurnConfig())
        self.assertEqual(config.cycles, 3)
        self.assertGreater(config.observe_seconds, 0)
        self.assertGreater(config.delay_seconds, 0)
        self.assertLessEqual(config.cycles, ChurnConfig.MAX_CYCLES)

    def test_rejects_invalid_or_unbounded_configuration(self) -> None:
        invalid = (
            {"cycles": 0},
            {"cycles": ChurnConfig.MAX_CYCLES + 1},
            {"cycles": True},
            {"observe_seconds": 0},
            {"max_events": 0},
            {"delay_seconds": 0},
            {"delay_seconds": float("inf")},
            {"concurrency": 2},
        )

        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                ChurnConfig.from_value(value)

    def test_settlement_schedule_must_be_positive_and_strictly_increasing(self) -> None:
        target = parse_target("dragon.local")
        for schedule in ((), (0.0,), (0.02, 0.01), (0.01, 0.01)):
            with self.subTest(schedule=schedule), self.assertRaises(ValueError):
                ChurnRunner(target, short_config(), settlement_schedule=schedule)


class ChurnRunnerTests(TestCase):
    def assert_clean(self, runner: ChurnRunner) -> None:
        self.assertEqual(runner.client.budget.active, 0)
        self.assertEqual(churn_threads(), [])
        self.assertTrue(runner.snapshot()["cleanup_complete"])

    def test_each_named_profile_starts_completes_and_releases_resources(self) -> None:
        with DeviceFixture({"event_count": 10}) as fixture:
            for name, config in ChurnConfig.profiles().items():
                with self.subTest(profile=name):
                    runner = ChurnRunner(parse_target(fixture.target), config)
                    runner.start()
                    wait_until(
                        lambda: runner.snapshot()["state"] == "completed",
                        timeout=12.0,
                    )
                    snapshot = runner.snapshot()
                    self.assertEqual(snapshot["profile"], name)
                    self.assertEqual(snapshot["configuration"], config.snapshot())
                    self.assertEqual(len(snapshot["cycles"]), config.cycles)
                    self.assert_clean(runner)

    def test_one_successful_cycle_disconnects_deliberately_and_preserves_health(self) -> None:
        health = b'{"boot_id":"boot-a","free_heap":1234,"minimum_free_heap":987,"sse_clients":1,"future":{"value":true}}'
        with DeviceFixture({"quiet_seconds": 0.8, "/api/v2/health": health}) as fixture:
            runner = ChurnRunner(parse_target(fixture.target), short_config())
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")
            snapshot = runner.snapshot()

        self.assertEqual(snapshot["successful_connections"], 1)
        self.assertEqual(snapshot["profile"], "Custom")
        self.assertEqual(snapshot["cycles"][0]["outcome"], "disconnected")
        self.assertEqual(snapshot["cycles"][0]["connection"]["state"], "open")
        self.assertIsInstance(snapshot["cycles"][0]["connection"]["elapsed_ms"], float)
        self.assertEqual(snapshot["cycles"][0]["close_reason"], "stopped")
        self.assertEqual(snapshot["initial_boot_id"], "boot-a")
        self.assertEqual(snapshot["latest_health"]["observed"]["free_heap"], 1234)
        self.assertEqual(
            snapshot["latest_health"]["observed"]["minimum_free_heap"], 987
        )
        self.assertIn('"future":{"value":true}', snapshot["latest_health"]["raw_payload"])
        kinds = [record["kind"] for record in runner.recorder.snapshot()]
        self.assertIn("churn_deliberate_disconnect", kinds)
        self.assertIn("churn_run_completed", kinds)
        self.assert_clean(runner)

    def test_multiple_cycles_remain_sequential_and_release_every_permit(self) -> None:
        with DeviceFixture({"quiet_seconds": 0.8}) as fixture:
            runner = ChurnRunner(parse_target(fixture.target), short_config(cycles=3))
            runner.start()
            max_owned = 0
            max_device = 0
            while runner.snapshot()["state"] != "completed":
                snapshot = runner.snapshot()
                max_owned = max(max_owned, snapshot["active_churn_connections"])
                max_device = max(max_device, snapshot["active_device_connections"])
                time.sleep(0.01)
            snapshot = runner.snapshot()

        self.assertEqual(snapshot["current_cycle"], 3)
        self.assertEqual(snapshot["successful_connections"], 3)
        self.assertLessEqual(max_owned, 1)
        self.assertLessEqual(max_device, 2)
        self.assert_clean(runner)

    def test_completed_run_settles_to_pre_run_client_baseline(self) -> None:
        health_sequence = [
            b'{"boot_id":"same","sse_clients":0,"free_heap_bytes":1000}',
            b'{"boot_id":"same","sse_clients":1,"free_heap_bytes":800}',
            b'{"boot_id":"same","sse_clients":1,"free_heap_bytes":820}',
            b'{"boot_id":"same","sse_clients":1,"free_heap_bytes":830}',
            b'{"boot_id":"same","sse_clients":1,"free_heap_bytes":840}',
            b'{"boot_id":"same","sse_clients":0,"free_heap_bytes":1000}',
        ]
        with DeviceFixture(
            {"quiet_seconds": 0.8, "health_sequence": health_sequence}
        ) as fixture:
            runner = ChurnRunner(
                parse_target(fixture.target),
                short_config(),
                settlement_schedule=(0.01, 0.02),
            )
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")
            snapshot = runner.snapshot()

        settlement = snapshot["settlement"]
        self.assertEqual(settlement["state"], "recovered")
        self.assertEqual(settlement["baseline_sse_clients"], 0)
        self.assertEqual(settlement["latest_sse_clients"], 0)
        self.assertEqual(len(settlement["samples"]), 2)
        self.assertEqual(settlement["samples"][-1]["free_heap"], 1000)
        kinds = [record["kind"] for record in runner.recorder.snapshot()]
        self.assertIn("churn_settlement_started", kinds)
        self.assertIn("churn_settlement_recovered", kinds)
        self.assertLess(
            kinds.index("churn_settlement_recovered"),
            kinds.index("churn_run_completed"),
        )
        self.assert_clean(runner)

    def test_cancelled_run_also_captures_settlement_recovery(self) -> None:
        health_sequence = [
            b'{"boot_id":"same","sse_clients":0,"free_heap_bytes":1000}',
            b'{"boot_id":"same","sse_clients":1,"free_heap_bytes":800}',
            b'{"boot_id":"same","sse_clients":1,"free_heap_bytes":850}',
            b'{"boot_id":"same","sse_clients":0,"free_heap_bytes":1000}',
        ]
        with DeviceFixture(
            {"quiet_seconds": 2.0, "health_sequence": health_sequence}
        ) as fixture:
            runner = ChurnRunner(
                parse_target(fixture.target),
                short_config(),
                settlement_schedule=(0.01, 0.02),
            )
            runner.start()
            wait_until(lambda: runner.snapshot()["active_churn_connections"] == 1)
            wait_until(lambda: any(
                record["kind"] == "churn_health_sample"
                and record["sample_point"] == "after_connection"
                for record in runner.recorder.snapshot()
            ))
            runner.stop(timeout=1.0)
            wait_until(lambda: runner.snapshot()["state"] == "cancelled")
            snapshot = runner.snapshot()

        self.assertEqual(snapshot["settlement"]["state"], "recovered")
        self.assertEqual(snapshot["settlement"]["latest_sse_clients"], 0)
        sample_points = [
            record["sample_point"]
            for record in runner.recorder.snapshot()
            if record["kind"] == "churn_health_sample"
        ]
        self.assertNotIn("after_disconnect", sample_points)
        self.assertNotIn("after_run", sample_points)
        self.assertEqual(sample_points.count("settlement"), 2)
        self.assert_clean(runner)

    def test_settlement_timeout_is_evidence_not_run_failure(self) -> None:
        health_sequence = [
            b'{"sse_clients":0}',
            b'{"sse_clients":1}',
            b'{"sse_clients":1}',
            b'{"sse_clients":1}',
            b'{"sse_clients":1}',
            b'{"sse_clients":1}',
        ]
        with DeviceFixture(
            {"quiet_seconds": 0.8, "health_sequence": health_sequence}
        ) as fixture:
            runner = ChurnRunner(
                parse_target(fixture.target),
                short_config(),
                settlement_schedule=(0.01, 0.02),
            )
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")
            snapshot = runner.snapshot()

        self.assertEqual(snapshot["settlement"]["state"], "timed_out")
        self.assertIsNone(snapshot["failure"])
        self.assertTrue(any(
            record["kind"] == "churn_settlement_timed_out"
            for record in runner.recorder.snapshot()
        ))
        self.assert_clean(runner)

    def test_additional_stop_interrupts_settlement_and_cancels_run(self) -> None:
        health_sequence = [
            b'{"sse_clients":0}',
            b'{"sse_clients":1}',
            b'{"sse_clients":1}',
            b'{"sse_clients":1}',
        ]
        with DeviceFixture(
            {"quiet_seconds": 0.8, "health_sequence": health_sequence}
        ) as fixture:
            runner = ChurnRunner(
                parse_target(fixture.target),
                short_config(),
                settlement_schedule=(0.5, 1.0),
            )
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "settling")
            self.assertFalse(runner.snapshot()["cleanup_complete"])
            runner.stop(timeout=1.0)
            wait_until(lambda: runner.snapshot()["state"] == "cancelled")
            snapshot = runner.snapshot()

        self.assertEqual(snapshot["settlement"]["state"], "interrupted")
        self.assertTrue(any(
            record["kind"] == "churn_settlement_interrupted"
            for record in runner.recorder.snapshot()
        ))
        self.assert_clean(runner)

    def test_heap_only_health_gets_one_delayed_noncomparable_sample(self) -> None:
        health = b'{"free_heap_bytes":1234}'
        with DeviceFixture(
            {"quiet_seconds": 0.8, "/api/v2/health": health}
        ) as fixture:
            runner = ChurnRunner(
                parse_target(fixture.target),
                short_config(),
                settlement_schedule=(0.01, 0.02),
            )
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")
            snapshot = runner.snapshot()

        self.assertEqual(snapshot["settlement"]["state"], "not_applicable")
        self.assertEqual(len(snapshot["settlement"]["samples"]), 1)
        self.assertEqual(snapshot["settlement"]["samples"][0]["free_heap"], 1234)
        self.assert_clean(runner)

    def test_capacity_rejection_is_evidence_not_run_failure(self) -> None:
        with DeviceFixture({"events_status": 503}) as fixture:
            runner = ChurnRunner(parse_target(fixture.target), short_config())
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")
            snapshot = runner.snapshot()

        self.assertEqual(snapshot["rejected_connections"], 1)
        self.assertEqual(snapshot["cycles"][0]["outcome"], "capacity_rejected")
        unavailable = next(
            record for record in runner.recorder.snapshot() if record["kind"] == "sse_unavailable"
        )
        self.assertEqual(unavailable["status"], 503)
        self.assertEqual(unavailable["parsed"]["future_detail"], True)
        self.assertEqual(unavailable["cycle"], 1)
        self.assert_clean(runner)

    def test_transport_failure_is_recorded_without_failing_controller(self) -> None:
        target = parse_target("127.0.0.1:9")
        recorder = SessionRecorder()
        client = DragonClient(
            target,
            recorder,
            request_timeout=0.1,
            sse_connect_timeout=0.1,
        )
        runner = ChurnRunner(target, short_config(), client=client)
        runner.start()
        wait_until(lambda: runner.snapshot()["state"] == "completed")
        snapshot = runner.snapshot()

        self.assertEqual(snapshot["transport_failures"], 1)
        self.assertEqual(snapshot["cycles"][0]["outcome"], "transport_failure")
        self.assertTrue(any(r["kind"] == "sse_error" for r in runner.recorder.snapshot()))
        self.assert_clean(runner)

    def test_controller_failure_is_recorded_and_does_not_escape_worker(self) -> None:
        target = parse_target("dragon.local")
        recorder = SessionRecorder()

        class BrokenClient(DragonClient):
            def fetch_json(self, path: str, *, context: dict[str, object] | None = None) -> dict[str, object]:
                raise RuntimeError("controlled test failure")

        runner = ChurnRunner(target, short_config(), client=BrokenClient(target, recorder))
        runner.start()
        wait_until(lambda: runner.snapshot()["state"] == "failed")
        snapshot = runner.snapshot()

        self.assertEqual(snapshot["failure"]["type"], "RuntimeError")
        failure = next(
            record
            for record in runner.recorder.snapshot()
            if record["kind"] == "churn_internal_failure"
        )
        self.assertEqual(failure["message"], "controlled test failure")
        self.assert_clean(runner)

    def test_cleanup_timeout_fails_run_without_starting_another_cycle(self) -> None:
        target = parse_target("dragon.local")
        recorder = SessionRecorder()
        release = threading.Event()

        class StuckClient(DragonClient):
            def fetch_json(
                self, path: str, *, context: dict[str, object] | None = None
            ) -> dict[str, object]:
                return {"status": 200, "parsed": {}, "raw_payload": "{}", "ok": True}

            def stream_events(self, stop, on_event, on_state, *, context=None):
                details = {
                    **(context or {}),
                    "request_id": 1,
                    "status": 200,
                    "elapsed_ms": 0.0,
                }
                on_state("open", details)
                release.wait(3)
                on_state("closed", {**details, "reason": "stopped"})

            def close_stream(self) -> None:
                pass

        client = StuckClient(target, recorder, sse_connect_timeout=0.01)
        runner = ChurnRunner(target, short_config(cycles=2), client=client)
        runner.start()
        wait_until(
            lambda: any(
                record["kind"] == "churn_cleanup_timeout"
                for record in runner.recorder.snapshot()
            )
        )
        wait_until(lambda: runner.snapshot()["state"] == "stopping")
        self.assertEqual(runner.snapshot()["current_cycle"], 1)
        release.set()
        wait_until(lambda: runner.snapshot()["state"] == "failed")
        snapshot = runner.snapshot()

        self.assertEqual(len(snapshot["cycles"]), 1)
        self.assertEqual(snapshot["cycles"][0]["outcome"], "cleanup_timeout")
        self.assertEqual(snapshot["local_resource_failures"], 1)
        self.assert_clean(runner)

    def test_comment_keepalive_is_preserved_but_not_counted_as_event(self) -> None:
        with DeviceFixture() as fixture:
            runner = ChurnRunner(parse_target(fixture.target), short_config())
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")
            snapshot = runner.snapshot()

        self.assertEqual(snapshot["events_observed"], 1)
        comments = [r for r in runner.recorder.snapshot() if r["kind"] == "sse_comment"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["cycle"], 1)
        self.assert_clean(runner)

    def test_event_bound_is_a_hard_dispatch_limit(self) -> None:
        config = ChurnConfig(
            cycles=1,
            observe_seconds=1.0,
            max_events=3,
            delay_seconds=0.1,
        )
        with DeviceFixture({"event_count": 50}) as fixture:
            runner = ChurnRunner(parse_target(fixture.target), config)
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")
            snapshot = runner.snapshot()

        self.assertEqual(snapshot["events_observed"], 3)
        disconnect = next(
            record
            for record in runner.recorder.snapshot()
            if record["kind"] == "churn_deliberate_disconnect"
        )
        self.assertEqual(disconnect["reason"], "event_bound")
        self.assert_clean(runner)

    def test_cancellation_during_blocked_read_is_bounded_and_cleans_up(self) -> None:
        with DeviceFixture({"quiet_seconds": 2.0}) as fixture:
            runner = ChurnRunner(parse_target(fixture.target), short_config())
            runner.start()
            wait_until(lambda: runner.snapshot()["active_churn_connections"] == 1)
            runner.stop(timeout=1.0)
            wait_until(lambda: runner.snapshot()["state"] == "cancelled")
            snapshot = runner.snapshot()

        self.assertEqual(snapshot["state"], "cancelled")
        self.assertEqual(snapshot["active_churn_connections"], 0)
        self.assertTrue(any(
            record["kind"] == "churn_run_cancelled" for record in runner.recorder.snapshot()
        ))
        sample_points = [
            record["sample_point"]
            for record in runner.recorder.snapshot()
            if record["kind"] == "churn_health_sample"
        ]
        self.assertNotIn("after_disconnect", sample_points)
        self.assertNotIn("after_run", sample_points)
        self.assert_clean(runner)

    def test_cancellation_during_connection_establishment_stays_truthful(self) -> None:
        with DeviceFixture({"events_connect_delay": 0.5, "quiet_seconds": 0.5}) as fixture:
            runner = ChurnRunner(parse_target(fixture.target), short_config())
            runner.start()
            wait_until(lambda: runner.snapshot()["cycles"])
            completed = runner.stop(timeout=0.05)
            self.assertFalse(completed)
            self.assertEqual(runner.snapshot()["state"], "stopping")
            wait_until(lambda: runner.snapshot()["state"] == "cancelled")

        self.assert_clean(runner)

    def test_cancellation_between_cycles_stops_scheduling_new_connections(self) -> None:
        with DeviceFixture({"quiet_seconds": 0.8}) as fixture:
            runner = ChurnRunner(parse_target(fixture.target), short_config(cycles=3, delay=1.0))
            runner.start()
            wait_until(lambda: len(runner.snapshot()["cycles"]) == 1 and runner.snapshot()["cycles"][0]["elapsed_ms"] is not None)
            runner.stop(timeout=1.0)
            wait_until(lambda: runner.snapshot()["state"] == "cancelled")
            snapshot = runner.snapshot()

        self.assertEqual(snapshot["current_cycle"], 1)
        self.assertEqual(len(snapshot["cycles"]), 1)
        self.assert_clean(runner)

    def test_absent_health_and_missing_optional_fields_do_not_fail_run(self) -> None:
        config = {
            "events_status": 503,
            "/api/v2/health": b'{"api_version":2}',
            "json_status": {"/api/v2/health": 404},
        }
        with DeviceFixture(config) as fixture:
            runner = ChurnRunner(parse_target(fixture.target), short_config())
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")
            snapshot = runner.snapshot()

        self.assertIsNone(snapshot["initial_boot_id"])
        self.assertEqual(snapshot["latest_health"]["status"], 404)
        self.assertEqual(snapshot["latest_health"]["observed"], {})
        self.assert_clean(runner)

    def test_boot_id_change_is_reported_without_inventing_a_cause(self) -> None:
        health_sequence = [
            b'{"boot_id":"boot-a","sse_clients":0}',
            b'{"boot_id":"boot-a","sse_clients":1}',
            b'{"boot_id":"boot-b","sse_clients":0}',
            b'{"boot_id":"boot-b","sse_clients":0}',
        ]
        with DeviceFixture({"quiet_seconds": 0.8, "health_sequence": health_sequence}) as fixture:
            runner = ChurnRunner(parse_target(fixture.target), short_config())
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")
            snapshot = runner.snapshot()

        self.assertTrue(snapshot["boot_id_changed"])
        self.assertEqual(snapshot["initial_boot_id"], "boot-a")
        self.assertEqual(snapshot["latest_boot_id"], "boot-b")
        self.assertEqual(len(snapshot["boot_id_changes"]), 1)
        change = next(r for r in runner.recorder.snapshot() if r["kind"] == "churn_boot_id_changed")
        self.assertNotIn("cause", change)
        self.assert_clean(runner)

    def test_unchanged_boot_id_and_reused_fd_are_not_false_failures(self) -> None:
        health = b'{"boot_id":"same","sse_connections":[{"connection_id":9,"fd":7}],"unknown":42}'
        with DeviceFixture({"quiet_seconds": 0.8, "/api/v2/health": health}) as fixture:
            runner = ChurnRunner(parse_target(fixture.target), short_config(cycles=2))
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")
            snapshot = runner.snapshot()

        self.assertFalse(snapshot["boot_id_changed"])
        self.assertIsNone(snapshot["failure"])
        self.assertEqual(snapshot["latest_health"]["parsed"]["unknown"], 42)
        self.assertEqual(
            snapshot["latest_health"]["observed"]["sse_connections"][0]["fd"], 7
        )
        self.assert_clean(runner)

    def test_jsonl_export_contains_correlated_raw_evidence(self) -> None:
        health = b'{"boot_id":"export","future":{"nested":[1,2]}}'
        with DeviceFixture({"events_status": 503, "/api/v2/health": health}) as fixture:
            runner = ChurnRunner(parse_target(fixture.target), short_config())
            runner.start()
            wait_until(lambda: runner.snapshot()["state"] == "completed")

        records = [json.loads(line) for line in runner.recorder.export_jsonl().splitlines()]
        self.assertTrue(all(record.get("run_id") == runner.run_id for record in records if record["kind"].startswith("churn_")))
        health_record = next(record for record in records if record["kind"] == "churn_health_sample")
        self.assertIn('"future":{"nested":[1,2]}', health_record["sample"]["raw_payload"])
        self.assert_clean(runner)
