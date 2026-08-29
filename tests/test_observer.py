import time
import threading
from typing import Callable
from unittest import TestCase

from dragonsniff.client import DragonClient
from dragonsniff.observer import Observer
from dragonsniff.recording import SessionRecorder
from dragonsniff.target import parse_target

from tests.test_client import DeviceFixture


def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


class ObserverTests(TestCase):
    def test_session_fetches_all_endpoints_and_streams_then_cleans_up(self) -> None:
        with DeviceFixture() as fixture:
            target = parse_target(fixture.target)
            recorder = SessionRecorder(max_records=50)
            client = DragonClient(target, recorder)
            observer = Observer(target, client=client)
            observer.start()
            wait_until(lambda: observer.snapshot()["sse"]["state"] == "closed")
            snapshot = observer.snapshot()
            observer.stop()

        self.assertEqual(
            [snapshot["http"][path]["state"] for path in snapshot["http"]],
            ["available", "available", "available"],
        )
        self.assertEqual(snapshot["sse"]["events"], 1)
        self.assertEqual(snapshot["limits"]["device_connection_limit"], 2)
        self.assertEqual(client.budget.active, 0)
        self.assertEqual(observer.snapshot()["session_state"], "stopped")

    def test_manual_reconnect_uses_a_new_generation_without_unbounded_threads(self) -> None:
        with DeviceFixture() as fixture:
            target = parse_target(fixture.target)
            observer = Observer(target)
            observer.start()
            wait_until(lambda: observer.snapshot()["sse"]["state"] == "closed")
            first_generation = observer.snapshot()["sse"]["generation"]
            observer.reconnect_events()
            wait_until(
                lambda: observer.snapshot()["sse"]["state"] == "closed"
                and observer.snapshot()["sse"]["generation"] > first_generation
            )
            snapshot = observer.snapshot()
            observer.stop()

        self.assertEqual(snapshot["sse"]["generation"], first_generation + 1)
        self.assertLessEqual(snapshot["limits"]["active_device_connections"], 2)

    def test_unavailable_stream_keeps_rejection_status_visible(self) -> None:
        with DeviceFixture({"events_status": 503}) as fixture:
            target = parse_target(fixture.target)
            observer = Observer(target)
            observer.start()
            wait_until(lambda: observer.snapshot()["sse"]["state"] == "unavailable")
            snapshot = observer.snapshot()
            observer.stop()

        self.assertEqual(snapshot["sse"]["details"]["status"], 503)
        self.assertEqual(snapshot["sse"]["close_details"]["reason"], "unavailable")

    def test_repeated_reconnect_and_stream_stop_release_workers_and_budget(self) -> None:
        with DeviceFixture({"quiet_seconds": 0.8}) as fixture:
            target = parse_target(fixture.target)
            observer = Observer(target)
            observer.start()
            wait_until(lambda: observer.snapshot()["sse"]["state"] == "open")

            for _ in range(3):
                observer.stop_events()
                wait_until(lambda: observer.snapshot()["sse"]["state"] == "stopped")
                self.assertEqual(observer.client.budget.active, 0)
                self.assertFalse(
                    any(thread.name.startswith("dragonsniff-sse-") for thread in threading.enumerate())
                )
                observer.reconnect_events()
                wait_until(lambda: observer.snapshot()["sse"]["state"] == "open")

            observer.stop_events()
            observer.stop()

        self.assertEqual(observer.client.budget.active, 0)
        self.assertFalse(any(thread.name.startswith("dragonsniff-sse-") for thread in threading.enumerate()))

    def test_stop_session_during_active_sse_read_completes_cleanup(self) -> None:
        with DeviceFixture({"quiet_seconds": 0.8}) as fixture:
            observer = Observer(parse_target(fixture.target))
            observer.start()
            wait_until(lambda: observer.snapshot()["sse"]["state"] == "open")

            completed = observer.stop(timeout=1.0)

        self.assertTrue(completed)
        self.assertEqual(observer.snapshot()["session_state"], "stopped")
        self.assertEqual(observer.client.budget.active, 0)
        self.assertFalse(any(
            thread.name.startswith("dragonsniff-") for thread in threading.enumerate()
        ))

    def test_stop_session_during_sse_connect_stays_stopping_until_cleanup(self) -> None:
        with DeviceFixture({"events_connect_delay": 0.35}) as fixture:
            observer = Observer(parse_target(fixture.target))
            observer.start()
            wait_until(lambda: observer.snapshot()["sse"]["state"] == "connecting")

            completed = observer.stop(timeout=0.05)
            self.assertFalse(completed)
            self.assertEqual(observer.snapshot()["session_state"], "stopping")
            self.assertFalse(observer.reconnect_events())
            self.assertFalse(observer.refresh())
            wait_until(lambda: observer.snapshot()["session_state"] == "stopped")

        self.assertEqual(observer.client.budget.active, 0)
        self.assertFalse(any(
            thread.name.startswith("dragonsniff-") for thread in threading.enumerate()
        ))

    def test_stop_session_during_json_refresh_stays_stopping_until_cleanup(self) -> None:
        with DeviceFixture() as fixture:
            observer = Observer(parse_target(fixture.target))
            observer.start()
            wait_until(lambda: observer.snapshot()["sse"]["state"] == "closed")
            fixture.server.config["json_delay_seconds"] = {"/api/v2/info": 0.35}  # type: ignore[attr-defined]
            self.assertTrue(observer.refresh())
            wait_until(
                lambda: observer.snapshot()["http"]["/api/v2/info"]["state"] == "requesting"
            )

            completed = observer.stop(timeout=0.05)
            self.assertFalse(completed)
            self.assertEqual(observer.snapshot()["session_state"], "stopping")
            self.assertFalse(observer.refresh())
            wait_until(lambda: observer.snapshot()["session_state"] == "stopped")

        self.assertEqual(observer.client.budget.active, 0)
        self.assertFalse(any(
            thread.name.startswith("dragonsniff-") for thread in threading.enumerate()
        ))
