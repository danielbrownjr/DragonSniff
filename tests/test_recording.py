import json
from unittest import TestCase

from dragonsniff.recording import SessionRecorder


class RecordingTests(TestCase):
    def test_export_is_ordered_jsonl_and_preserves_unknown_payloads(self) -> None:
        recorder = SessionRecorder(max_records=3)
        recorder.append("http_response", parsed={"future_field": {"value": 17}})
        recorder.append("sse_event", raw_payload="event: strange\ndata: opaque\n\n")

        lines = recorder.export_jsonl().splitlines()
        self.assertEqual(len(lines), 2)
        records = [json.loads(line) for line in lines]
        self.assertEqual([record["sequence"] for record in records], [1, 2])
        self.assertEqual(records[0]["parsed"]["future_field"]["value"], 17)
        self.assertEqual(records[1]["raw_payload"], "event: strange\ndata: opaque\n\n")
        self.assertIn("timestamp", records[0])
        self.assertIn("monotonic_ns", records[0])

    def test_recording_is_bounded_and_reports_drops(self) -> None:
        recorder = SessionRecorder(max_records=2)
        recorder.append("one")
        recorder.append("two")
        recorder.append("three")
        self.assertEqual([record["kind"] for record in recorder.snapshot()], ["two", "three"])
        self.assertEqual(
            recorder.summary(), {"records": 2, "max_records": 2, "dropped_records": 1}
        )

    def test_record_input_is_copied(self) -> None:
        recorder = SessionRecorder()
        payload = {"nested": [1]}
        recorder.append("sample", payload=payload)
        payload["nested"].append(2)
        self.assertEqual(recorder.snapshot()[0]["payload"], {"nested": [1]})
