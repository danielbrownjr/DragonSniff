import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import time
from unittest import TestCase
from unittest.mock import patch

from dragonsniff.storage import IO_CHUNK_BYTES, SessionStore, export_filename


def evidence_bytes(store: SessionStore, session_id: str) -> bytes | None:
    with store.lease_evidence(session_id) as stream:
        return stream.read() if stream is not None else None


class SessionStoreTests(TestCase):
    def test_records_are_persisted_incrementally_and_finish_cleanly(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("capture", "http://dragon.local", 10)

            recorder.append("capture_run_started", run_id="run")
            on_disk = evidence_bytes(store, recorder.session_id)
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
            self.assertEqual(len(evidence_bytes(store, recorder.session_id).splitlines()), 3)
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
            self.assertEqual(
                len(evidence_bytes(recovered, recorder.session_id).splitlines()), 1
            )

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
            with store.lease_evidence("0" * 32) as stream:
                self.assertIsNone(stream)

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
                for line in evidence_bytes(store, recorder.session_id).splitlines()
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
            metadata = store.get_session(recorder.session_id)
            self.assertEqual(metadata["status"], "failed")
            self.assertIn("disk full", metadata["failure"])
            self.assertIsNotNone(metadata["finished_at"])

            recovered = SessionStore(temporary)
            self.assertEqual(
                recovered.get_session(recorder.session_id)["status"], "failed"
            )
            with self.assertRaisesRegex(RuntimeError, "finished"):
                recorder.append("sample")

    def test_terminal_metadata_checkpoint_includes_all_records(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("capture", "http://dragon.local", 100)
            for value in range(10):
                recorder.append("sample", value=value)
            metadata_path = (
                Path(temporary) / "sessions" / recorder.session_id / "metadata.json"
            )
            checkpoint = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["records"], 0)

            recorder.append("capture_run_completed")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["records"], 11)
            self.assertEqual(metadata["status"], "completed")

    def test_no_op_restart_preserves_terminal_metadata_mtime(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("observation", "http://dragon.local", 10)
            recorder.append("session_stopped")
            metadata_path = (
                Path(temporary) / "sessions" / recorder.session_id / "metadata.json"
            )
            before = metadata_path.stat().st_mtime_ns
            time.sleep(0.01)

            SessionStore(temporary)

            self.assertEqual(metadata_path.stat().st_mtime_ns, before)

    def test_large_partial_tail_recovery_uses_bounded_reads(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            recorder = store.create_recorder("observation", "http://dragon.local", 10)
            recorder.append("session_started")
            evidence = (
                Path(temporary) / "sessions" / recorder.session_id / "evidence.jsonl"
            )
            with evidence.open("ab") as stream:
                stream.write(b"x" * (IO_CHUNK_BYTES * 3 + 7))

            original_open = Path.open
            read_sizes: list[int] = []

            class TrackingReader:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    self.stream.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.stream.__exit__(*args)

                def __getattr__(self, name):
                    return getattr(self.stream, name)

                def read(self, size=-1):
                    read_sizes.append(size)
                    if size < 0:
                        raise AssertionError("startup repair attempted an unbounded read")
                    return self.stream.read(size)

            def tracked_open(path, mode="r", *args, **kwargs):
                stream = original_open(path, mode, *args, **kwargs)
                if path == evidence and "b" in mode:
                    return TrackingReader(stream)
                return stream

            with patch.object(Path, "open", tracked_open):
                recovered = SessionStore(temporary)

            self.assertTrue(read_sizes)
            self.assertLessEqual(max(read_sizes), IO_CHUNK_BYTES)
            self.assertEqual(
                recovered.get_session(recorder.session_id)["recovered_partial_bytes"],
                IO_CHUNK_BYTES * 3 + 7,
            )

    def test_download_lease_defers_retention_of_open_session(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(
                temporary, retention_bytes=1_000_000, retention_sessions=1
            )
            first = store.create_recorder("observation", "http://one.local", 10)
            first.append("session_stopped")
            with store.lease_evidence(first.session_id) as stream:
                second = store.create_recorder("observation", "http://two.local", 10)
                second.append("session_stopped")
                self.assertIsNotNone(stream)
                self.assertIsNotNone(store.get_session(first.session_id))
            self.assertIsNone(store.get_session(first.session_id))

    def test_retention_deletion_failure_is_nonfatal_and_retried(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(
                temporary, retention_bytes=1_000_000, retention_sessions=1
            )
            first = store.create_recorder("observation", "http://one.local", 10)
            first.append("session_stopped")
            second = store.create_recorder("observation", "http://two.local", 10)
            with patch("dragonsniff.storage.shutil.rmtree", side_effect=OSError("busy")):
                with self.assertLogs("dragonsniff.storage", level="WARNING"):
                    second.append("session_stopped")
            self.assertIsNotNone(store.get_session(first.session_id))
            self.assertEqual(store.get_session(second.session_id)["status"], "completed")
            store.enforce_retention()
            self.assertEqual(len(store.list_sessions()), 1)

    def test_export_filename_revalidates_and_sanitizes_header_components(self) -> None:
        metadata = {
            "session_id": "a" * 32,
            "kind": "capture",
            "created_at": "2026-09-07T12:00:00\r\nX-Evil: yes",
        }
        filename = export_filename(metadata)
        self.assertRegex(filename, r"^[A-Za-z0-9._-]+$")
        self.assertNotIn("\r", filename)
        self.assertNotIn("\n", filename)
        with self.assertRaises(ValueError):
            export_filename({**metadata, "kind": "capture\r\nX-Evil: yes"})

    def test_session_id_validator_rejects_unicode_lookalikes(self) -> None:
        with TemporaryDirectory() as temporary:
            store = SessionStore(temporary)
            invalid = (
                "ａ" * 32,
                "０" * 32,
                "a" * 31 + "é",
                "A" * 32,
            )
            for session_id in invalid:
                with self.subTest(session_id=session_id):
                    self.assertIsNone(store.get_session(session_id))
                    with store.lease_evidence(session_id) as stream:
                        self.assertIsNone(stream)
