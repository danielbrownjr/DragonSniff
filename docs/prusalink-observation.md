# Optional PrusaLink observations

DragonSniff can add read-only PrusaLink status observations to the same recorder and JSONL timeline as Dragon-family evidence. The source is disabled by default. It runs during live observation and Thermal capture, and pauses during Churn so Churn's connection exercise remains isolated.

This feature is evidence acquisition only. It cannot change a bed or nozzle target, start or stop a print, pause or resume a printer, select a Jump Jet mode, command a heater or fan, or reconstruct Jump Jet Automatic policy.

## Configuration

Set a PrusaLink origin and API key before starting DragonSniff:

```console
export DRAGONSNIFF_PRUSALINK_URL=http://prusa.local
export DRAGONSNIFF_PRUSALINK_API_KEY_FILE=/run/secrets/prusalink_api_key
dragonsniff
```

`DRAGONSNIFF_PRUSALINK_API_KEY` is available for trusted development environments, but a permissions-restricted secret file is preferred for persistent or container deployment. Configure only one key source. The API key has no command-line option, is never returned by the local API, and is never placed in JSONL or log messages. Credentials embedded in a URL are rejected.

The URL may also be supplied with `--prusalink-url`. `--prusalink-poll-interval` or `DRAGONSNIFF_PRUSALINK_POLL_INTERVAL` selects a 1–60 second cadence; the default is 5 seconds. Failures use exponential retry delay capped at 60 seconds, so an unavailable or unauthorized printer is not busy-polled.

DragonSniff makes exactly one PrusaLink request:

- `GET /api/v1/status`, authenticated with `X-Api-Key`

No generic PrusaLink proxy or write-capable route exists.

## Captured fields and evidence envelope

A usable response must contain the complete core trio: a `printer` object with a non-blank string `state` (at most 128 characters) and finite numeric `temp_bed` and `target_bed` fields. If any core field is absent or invalid, the entire poll is a schema failure and does not refresh the last-good timestamp. Unknown valid printer-state strings are preserved as observations; DragonSniff does not interpret them as policy.

`temp_nozzle` and `target_nozzle` are independent best-effort fields. A valid finite field is retained, an absent field is omitted, and a malformed or non-finite field is omitted without invalidating a good core sample. The record's bounded `omitted_optional_fields` list identifies malformed optional fields. No value is coerced, invented, or carried forward independently.

Each attempt appends one `source_observation` record. Its principal fields have these semantics:

| Field | Meaning |
|---|---|
| `sequence` | Authoritative global arrival/commit order across Dragon and external-source records in the shared recorder. |
| `timestamp` | DragonSniff UTC recorder-ingest timestamp assigned when the record is appended. |
| `observed_at` | DragonSniff UTC request-start timestamp for this PrusaLink attempt. |
| `source` | Stable source type; currently `"prusalink"`. |
| `source_id` | Normalized identity of the configured PrusaLink origin. It contains no credentials. |
| `source_state` | Lifecycle/result meaning such as `healthy`, `stale`, `auth_error`, or `parse_error`. It is not an HTTP status. |
| `response_status` | Numeric HTTP response status when one was received; otherwise `null`. |
| `freshness` | `fresh`, `stale`, or `unavailable`, plus age of the last successful core sample. |
| `data` | Source-specific admitted fields from a successful core sample. |
| `omitted_optional_fields` | Bounded names of malformed optional fields omitted from an otherwise healthy core sample. |

Records also include the corresponding monotonic timestamps, elapsed request time, fixed `endpoint` and `method: "GET"`, connection/authentication observations, and structured error information. Thermal records include the capture `run_id` and owner.

PrusaLink does not provide a source timestamp in this endpoint. Both `observed_at` and `timestamp` therefore come from DragonSniff; no printer-side timestamp is invented.

The response body is not retained. This intentionally limits the source to the documented fields and prevents an untrusted endpoint from reflecting the configured credential into evidence. A printer-state value exactly equal to the configured credential is rejected as a structured sample rather than recorded. Equality avoids false rejection when a short, valid key happens to be a substring of an ordinary state such as `IDLE`; PrusaLink's accepted key handling does not provide a documented minimum length on which DragonSniff could safely rely.

`source_observation` is an additive record kind in the existing format-version 1 JSONL stream. Existing records and readers remain valid; consumers that do not recognize the new kind may ignore it. Thermal recorders add source polling headroom to the existing Dragon schedule and 1,000-annotation reserve. The source estimate is `min(3,602, ceil((duration_seconds + 20) / poll_interval_seconds) + 2)`: the 20-second term is the single capture-boundary allowance, and the named 3,602-record cap matches the one-hour live source horizon. Smoke at one-second polling adds 142 records. Long Haul at one-second polling is capped at 3,602 rather than adding roughly 28,822 records. The complete Dragon scheduled estimate and annotation headroom remain in the recorder capacity; if actual source attempts exceed the capped estimate, the single FIFO may roll older records from the in-memory view. Persistent JSONL remains append-only and retains those records.

Live observation adds a deterministic Prusa capacity allowance for a one-hour rolling diagnostic horizon, plus two scheduling-boundary records. The formula is `2,000 + min(3,602, ceil(3,600 / poll_interval_seconds) + 2)`. The total capacities are therefore 5,602 records at one-second polling, 2,722 at the default five seconds, and 2,062 at 60 seconds. This approximately preserves the existing live-observation history horizon under the observed Dragon cadence while keeping both sources on one arrival-ordered rolling timeline. It is not a guarantee that 2,000 Dragon records remain: all record kinds participate equally in normal FIFO eviction. The expansion is bounded to 3,602 additional normalized records (roughly one hour at the densest supported source cadence), records retain one shared sequence, and normalized source records never retain the response body.

## Freshness and failure behavior

The last successful sample is fresh for the greater of 15 seconds or three configured poll intervals, capped at 180 seconds. The source snapshot always reports `fresh`, `stale`, or `unavailable` with an age. Last-known data may remain visible after a failed attempt, but its freshness and the current source error are shown separately; it is never presented as a new sample.

The `source_state` lifecycle includes `disabled`, `configured`, `connecting`, `healthy`, `stale`, `auth_error`, `transport_error`, `parse_error`, and `internal_error`. Churn reports the configured source as `paused`. The separate `polling` boolean distinguishes active polling from not polling; `source_state` explains why the source is disabled, paused, stale, failed, or otherwise inactive. Syntax errors, unsafe decoded text, missing fields, and non-finite numeric values are recorded as failed observations. Expected source failures retry with bounded backoff. An unexpected internal exception records/surfaces `internal_error`, logs a credential-redacted traceback, and stops that source worker rather than silently dying or retrying a programmer error forever. Dragon observation or Thermal capture continues.

The connection and authentication booleans describe the most recent request attempt; PrusaLink polling does not maintain a persistent connection. An oversized response remains evidence of an HTTP-reachable source but is rejected as an unusable structured sample.

Persistent sessions append Prusa observations to the existing `evidence.jsonl`. Normal interrupted-session recovery, History detail, History export, retention, and immutable closed-session behavior apply without a separate Prusa log.

PrusaLink is the first external observation source. The small provenance envelope does not prevent later sources such as a thermocouple logger or bench instrument, but this implementation adds none of them.
