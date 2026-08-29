import time
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
        self.assertEqual(snapshot["sse"]["events"], 2)
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
