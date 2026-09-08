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

The browser keeps an unresolved annotation request in session storage and retries the same UUID after local-service connectivity returns. The server returns the original record for an identical retry and rejects reuse of that UUID with different content. Idempotency uses the request normalized to the authoritative active-capture identity: a null `capture_session_id` is stamped before comparison and storage, while a supplied mismatched ID is rejected. Server-derived timestamps, sequence, and capture-relative time are not client content. With persistent storage enabled, the same resolution works after a DragonSniff restart by checking the identified capture evidence. A definitive rejection is reported as not recorded.

New annotations are rejected before capture start and once the capture enters `stopping` or a terminal state. DragonSniff does not currently expose capture pause/resume, so there is no ambiguous paused boundary. A completed or interrupted persistent capture retains every already-accepted annotation in History and its original JSONL export.

Each capture reserves room for exactly 1,000 annotations in addition to its validated scheduled-record estimate. Valid annotations 1 through 1,000 are recorded. Annotation 1,001 and later are explicitly rejected as not recorded; earlier annotations are never evicted, and telemetry keeps its separately reserved capacity. Retrying an accepted UUID still resolves to its original record after the limit, while retrying an over-limit UUID is rejected again.

Freeform `operator_note` content must contain at least one non-whitespace character. Accepted content is otherwise preserved exactly, including leading/trailing whitespace, combining marks, multilingual text, and supplementary-plane characters. Every persisted text field must be encodable as UTF-8; JSON lone-surrogate escapes are rejected before the recorder is called.

`external_correlation.known_offset_ms` must be finite and within ±1,000,000,000 ms. This generous sanity bound retains plausible cross-system clock offsets while rejecting accidental or abusive magnitudes.

## Quick-pick markers

The initial marker vocabulary is:

`fan_blocked`, `fan_unblocked`, `airflow_partial`, `airflow_restored`, `thermistor_unplugged`, `thermistor_reconnected`, `chamber_opened`, `chamber_closed`, `printer_stopped`, `bed_target_changed`, `printer_link_lost`, `jumpjet_power_off`, `jumpjet_power_on`, `stimulus_applied`, `stimulus_removed`, `baseline_start`, `recovery_start`, `external_log_start`, `scope_trigger`, `flir_capture`, `abort`, and `operator_intervention`.

These are observations, not device commands or fault injection.
