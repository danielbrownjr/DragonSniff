# Operator annotations

Thermal captures accept local operator markers while the capture state is `running`. A quick-pick button records its marker immediately; the freeform form records an `operator_note`. A prepared note and optional external-correlation fields are included with either action.

Annotations are DragonSniff evidence only. The browser sends `POST /local/v1/capture/annotations` to the local DragonSniff service. That path appends to the active capture recorder and does not call the observed device. DragonSniff's device traffic remains limited to the documented read-only `GET` endpoints.

## Evidence record

Every accepted annotation is stored once as an `operator_annotation` JSONL record with the recorder's normal `sequence`, UTC `timestamp`, and `monotonic_ns`. It also contains:

- `annotation_id`: browser-generated UUID used for idempotent retry
- `run_id` and `capture_session_id`: capture identity
- `capture_relative_ms`: time since this capture started
- `marker`: stable machine-readable marker type
- `note`: exact submitted text, including Unicode and whitespace
- `source`: `operator`
- `operator`: optional operator identity
- `time_basis`: `operator_submission_received`
- `external_correlation`: optional instrument, file/reference, clock-sync method, and known clock offset

The timestamps describe when DragonSniff received the operator action. They do not claim to be the exact physical event time. A known external offset is retained as submitted and does not produce an invented corrected timestamp.

Recorder sequence is the authoritative ordering shared with telemetry records. Export reads the stored record; it does not regenerate or reinterpret annotations.

## Delivery and boundaries

The browser keeps an unresolved annotation request in session storage and retries the same UUID after local-service connectivity returns. The server returns the original record for an identical retry and rejects reuse of that UUID with different content. With persistent storage enabled, the same resolution works after a DragonSniff restart by checking the identified capture evidence. A definitive rejection is reported as not recorded.

New annotations are rejected before capture start and once the capture enters `stopping` or a terminal state. DragonSniff does not currently expose capture pause/resume, so there is no ambiguous paused boundary. A completed or interrupted persistent capture retains every already-accepted annotation in History and its original JSONL export.

Each capture reserves room for up to 1,000 annotations in addition to its validated scheduled-record estimate.

## Quick-pick markers

The initial marker vocabulary is:

`fan_blocked`, `fan_unblocked`, `airflow_partial`, `airflow_restored`, `thermistor_unplugged`, `thermistor_reconnected`, `chamber_opened`, `chamber_closed`, `printer_stopped`, `bed_target_changed`, `printer_link_lost`, `jumpjet_power_off`, `jumpjet_power_on`, `stimulus_applied`, `stimulus_removed`, `baseline_start`, `recovery_start`, `external_log_start`, `scope_trigger`, `flir_capture`, `abort`, and `operator_intervention`.

These are observations, not device commands or fault injection.
