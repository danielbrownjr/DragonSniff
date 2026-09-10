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

Each attempt appends one `source_observation` record with:

- normal recorder `sequence`, UTC `timestamp`, and `monotonic_ns`
- `source: "prusalink"` and a normalized `source_id`
- fixed `endpoint` and `method: "GET"`
- DragonSniff request-start `observed_at` and `observed_monotonic_ns`
- request elapsed time, HTTP status, connection/authentication state, and error class
- freshness state and sample age
- a source-specific `data` object containing only the admitted fields
- capture `run_id` and owner when the record belongs to a Thermal capture

PrusaLink does not provide a source timestamp in this endpoint. Both observation and recorder-ingest timestamps therefore come from DragonSniff; no printer-side timestamp is invented.

The response body is not retained. This intentionally limits the source to the documented fields and prevents an untrusted endpoint from reflecting the configured credential into evidence. A response whose admitted text overlaps the credential is rejected as a structured sample rather than repaired or recorded.

`source_observation` is an additive record kind in the existing format-version 1 JSONL stream. Existing records and readers remain valid; consumers that do not recognize the new kind may ignore it. Thermal recorders reserve worst-case source polling headroom—including the bounded initial/final Dragon fetch window—separately from the existing Dragon schedule and 1,000-annotation reserve.

Live observation keeps the established 2,000-record Dragon baseline and adds a deterministic Prusa reserve for a one-hour rolling diagnostic horizon, plus two scheduling-boundary records. The formula is `2,000 + min(3,602, ceil(3,600 / poll_interval_seconds) + 2)`. The total capacities are therefore 5,602 records at 1-second polling, 2,722 at the default 5 seconds, and 2,062 at 60 seconds. One hour corresponds approximately to the existing live window under the currently observed two-second Dragon event cadence without turning that device cadence into a protocol promise. The expansion is capped, records retain one shared sequence, and normalized source records never retain the response body.

## Freshness and failure behavior

The last successful sample is fresh for the greater of 15 seconds or three configured poll intervals, capped at 180 seconds. The source snapshot always reports `fresh`, `stale`, or `unavailable` with an age. Last-known data may remain visible after a failed attempt, but its freshness and the current source error are shown separately; it is never presented as a new sample.

Source lifecycle states include `disabled`, `configured`, `connecting`, `healthy`, `stale`, `auth_error`, `transport_error`, and `parse_error`. Churn reports the configured source as `paused`. Syntax errors, unsafe decoded text, missing fields, and non-finite numeric values are recorded as failed observations. None of these failures aborts Dragon observation or Thermal capture.

The connection and authentication booleans describe the most recent request attempt; PrusaLink polling does not maintain a persistent connection. An oversized response remains evidence of an HTTP-reachable source but is rejected as an unusable structured sample.

Persistent sessions append Prusa observations to the existing `evidence.jsonl`. Normal interrupted-session recovery, History detail, History export, retention, and immutable closed-session behavior apply without a separate Prusa log.

PrusaLink is the first external observation source. The small provenance envelope does not prevent later sources such as a thermocouple logger or bench instrument, but this implementation adds none of them.
