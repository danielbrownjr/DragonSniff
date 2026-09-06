import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from unittest.mock import patch

from dragonsniff.storage import SessionStore


class SessionStoreTests(TestCase):
    def test_records_are_persisted_incrementally_and_finish_cleanly(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("capture", "http://dragon.local", 10)

            recorder.append("capture_run_started", run_id="run")
            on_disk = store.export_jsonl(recorder.session_id)
            self.assertIsNotNone(on_disk)
            self.assertEqual(json.loads(on_disk.splitlines()[0])["kind"], "capture_run_started")

            recorder.append("capture_run_completed", run_id="run")
            metadata = store.get_session(recorder.session_id)
            self.assertEqual(metadata["status"], "completed")
            self.assertEqual(metadata["records"], 2)
            self.assertEqual(recorder.summary()["persistent_session_id"], recorder.session_id)

    def test_disk_history_keeps_records_dropped_from_bounded_live_view(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("observation", "http://dragon.local", 2)
            recorder.append("one")
            recorder.append("two")
            recorder.append("three")
            self.assertEqual([item["kind"] for item in recorder.snapshot()], ["two", "three"])
            self.assertEqual(len(store.export_jsonl(recorder.session_id).splitlines()), 3)
            self.assertEqual(store.get_session(recorder.session_id)["records"], 3)

    def test_each_run_kind_maps_its_terminal_record_to_history_status(self) -> None:
        cases = (
            ("observation", "session_stopped", "completed"),
            ("capture", "capture_run_cancelled", "cancelled"),
            ("churn", "churn_run_failed", "failed"),
        )
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            for kind, terminal, expected in cases:
                recorder = store.create_recorder(kind, "http://dragon.local", 10)
                recorder.append(terminal)
                self.assertEqual(store.get_session(recorder.session_id)["status"], expected)

    def test_active_session_is_marked_interrupted_after_restart(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("observation", "http://dragon.local", 10)
            recorder.append("session_started")

            recovered = SessionStore(temporary)
            metadata = recovered.get_session(recorder.session_id)
            self.assertEqual(metadata["status"], "interrupted")
            self.assertIn("service startup", metadata["recovery"])
            self.assertEqual(len(recovered.export_jsonl(recorder.session_id).splitlines()), 1)

    def test_recovery_reconciles_metadata_after_evidence_only_crash_window(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("observation", "http://dragon.local", 10)
            recorder.append("session_started")
            metadata_path = (
                Path(temporary) / "sessions" / recorder.session_id / "metadata.json"
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["records"] = 0
            metadata["bytes"] = 0
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

            recovered = SessionStore(temporary)
            corrected = recovered.get_session(recorder.session_id)
            self.assertEqual(corrected["records"], 1)
            self.assertGreater(corrected["bytes"], 0)

    def test_partial_final_record_is_quarantined_during_recovery(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("observation", "http://dragon.local", 10)
            recorder.append("session_started")
            path = Path(temporary) / "sessions" / recorder.session_id / "evidence.jsonl"
            with path.open("ab") as stream:
                stream.write(b'{"incomplete":')

            recovered = SessionStore(temporary)
            metadata = recovered.get_session(recorder.session_id)
            self.assertEqual(metadata["recovered_partial_bytes"], len(b'{"incomplete":'))
            self.assertTrue(path.read_bytes().endswith(b"\n"))
            self.assertEqual(
                (path.parent / "evidence.partial").read_bytes(), b'{"incomplete":'
            )

    def test_retention_removes_oldest_finished_session_not_active_session(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary, retention_bytes=1_000_000)
            oldest = store.create_recorder("observation", "http://one.local", 10)
            oldest.append("session_started", payload="x" * 1_000)
            oldest.append("session_stopped")
            active = store.create_recorder("observation", "http://two.local", 10)
            active.append("session_started", payload="y" * 1_000)
            active_size = sum(
                item.stat().st_size
                for item in (Path(temporary) / "sessions" / active.session_id).iterdir()
            )
            store.retention_bytes = active_size

            store.enforce_retention()

            self.assertIsNone(store.get_session(oldest.session_id))
            self.assertIsNotNone(store.get_session(active.session_id))

    def test_retention_bounds_finished_session_count(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(
                temporary, retention_bytes=1_000_000, retention_sessions=2
            )
            identifiers = []
            for _ in range(3):
                recorder = store.create_recorder(
                    "observation", "http://dragon.local", 10
                )
                identifiers.append(recorder.session_id)
                recorder.append("session_started")
                recorder.append("session_stopped")
            self.assertEqual(len(store.list_sessions()), 2)
            self.assertIsNone(store.get_session(identifiers[0]))

    def test_invalid_or_unknown_session_id_is_not_exported(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            self.assertIsNone(store.get_session("../metadata"))
            self.assertIsNone(store.export_jsonl("0" * 32))

    def test_concurrent_appends_remain_in_sequence_order_on_disk(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("observation", "http://dragon.local", 100)
            threads = [
                Thread(target=lambda value=index: recorder.append("sample", value=value))
                for index in range(25)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            records = [
                json.loads(line)
                for line in store.export_jsonl(recorder.session_id).splitlines()
            ]
            self.assertEqual([record["sequence"] for record in records], list(range(1, 26)))

    def test_failed_durable_write_is_not_exposed_in_live_recorder(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("observation", "http://dragon.local", 10)
            with patch.object(store, "append", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(RuntimeError, "could not persist"):
                    recorder.append("sample")
            self.assertEqual(recorder.snapshot(), [])
            self.assertEqual(recorder.summary()["records"], 0)
            successful = recorder.append("sample")
            self.assertEqual(successful["sequence"], 1)
