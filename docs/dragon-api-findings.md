# Dragon API findings for DragonSniff V1

This note records what DragonSniff found in the ecosystem before choosing its V1 architecture. It is an observation of current products, not a new frozen Dragon API specification.

## Sources inspected

- `justinh-rahb/dragon-core` `origin/main` at `4c04c7a1ffc5c61c31e66313af9515c45f6fba0c`
- `danielbrownjr/DragonBreath` main at `30e880b49c6385ef2b48202b6119bd8109c66dff` and current PID/UI feature at `bba937b768069de5400d87e4a4eba7791399fa06`
- `danielbrownjr/JumpJet` foundation feature at `9097b27b46544b26b5e66830b058071bb439edc3`

The current upstream dragon-core does not own product `/api/v2` response handlers. Its `dc_ui` component documents the browser-side expectation and treats `/api/v2/events` as optional, with state polling as a fallback. DragonBreath and Jump Jet own their handlers.

## Observed contracts

### `GET /api/v2/info`

Both inspected products return JSON and currently share:

- `api_version` with numeric value `2`
- `device_id`
- `firmware`
- `project`
- `ui.schema`
- `ui.product`
- `ui.display_name`
- a `capabilities` array

DragonBreath also returns `boot_id`, thermistor reference diagnostics, inactive OTA-slot metadata, and release-update metadata. Jump Jet currently omits `boot_id`. Capability values are product-specific; consumers must not infer that a capability seen on one product is universal.

### `GET /api/v2/state`

Both products currently return JSON with `api_version: 2`. Everything beyond that is a product snapshot rather than a stable common schema. DragonBreath reports its policy mode, lease, sensors, outputs, safety and fault state, controller diagnostics, environment, printer state, and a `state_revision`. Jump Jet reports its deliberately cold-safe mode, heater and fan state, interlock state, and printer status.

Temperatures and other unavailable measurements may be JSON `null`. Fields may be absent. DragonSniff therefore preserves and displays the complete raw response, and its parsed view is schema-free.

### `GET /api/v2/health`

Endpoint shape is product-specific. DragonBreath reports `api_version`, `boot_id`, uptime, heap information, Wi-Fi information, and SSE-client information; its current validation feature adds further temporary resource diagnostics. Jump Jet currently returns only `status: cold_safe` and `heater_available: false`.

Health keys are observations, not a portable required-field list.

### `GET /api/v2/events`

SSE is optional. DragonBreath implements it and advertises `sse`; Jump Jet's current foundation does not register the route and advertises polling instead.

The inspected DragonBreath implementation:

- responds as `text/event-stream` with `Cache-Control: no-cache`
- sends a named `state` event on connection and whenever `state_revision` changes
- sends a full named `telemetry` snapshot every two seconds when the revision is unchanged
- uses the same product state payload for event data
- caps the registry at two concurrent SSE clients
- returns HTTP 503 with a JSON error payload when no stream slot is available
- cleans task-backed client slots after peer disconnect or send failure

The current feature branch contains additional SSE lifecycle diagnostics, but those do not define the general API contract. DragonSniff records arbitrary event names, IDs, comment-only transport blocks, data, parse failures, connection transitions, HTTP rejection bodies, and end-of-stream without assuming DragonBreath's event vocabulary. Comment-only blocks are retained as lifecycle evidence rather than dispatched or counted as application events.

## Error and availability handling

DragonBreath JSON error responses include product policy state in addition to an error code and message. An unavailable Jump Jet route follows its HTTP server's normal not-found behavior. Network failure, HTTP rejection, malformed JSON, missing routes, and clean SSE end-of-stream are distinct observations in DragonSniff's session.

DragonSniff does not silently substitute polling for SSE. It fetches `/state` during the initial JSON pass and permits explicit refreshes, while leaving the stream's unavailable or closed state visible. This is intentional for diagnostic evidence and leaves automated churn behavior to Issue #2.

## Architecture consequence

The products do not expose browser CORS headers as a common contract, and they should not need to. DragonSniff therefore uses a loopback-only host backend. It allows the browser to inspect an authorized local Dragon without adding developer-tool policy or allocations to firmware.

The backend has fixed read-only device routes, two device-connection permits, bounded bodies/events/session history, and no cloud or discovery behavior. The two permits are DragonSniff's own conservative resource budget and do not mirror or depend on DragonBreath's current two-client SSE cap. Its recorder and client lifecycle are separate from the UI, and the bounded churn runner reuses them without browser tabs.

SSE connection establishment is bounded to five seconds. Once established, a stream has no DragonSniff application-level inactivity timeout: SSE permits valid quiet streams, and DragonBreath's current two-second telemetry cadence is not assumed to be a family-wide contract. Explicit Stop or Reconnect closes the socket; transport failures remain recorded as errors unless the stream-specific stop condition is set. DragonSniff does not automatically reconnect.

The Issue #2 churn runner reuses these same fixed routes, recorder, parser, timeout semantics, and two-permit client budget. It opens at most one churn-owned SSE connection at a time. The second permit allows a bounded health sample while that stream is open; it is not a claim about device-side stream capacity.

Churn records HTTP 503 stream rejection without assuming every 503 has the same product cause. DragonBreath's current valid JSON `busy` response is preserved as one real-world example. Other HTTP statuses, invalid bodies, transport failures, remote EOF, deliberate disconnect, cancellation, and controller failures remain distinguishable evidence.
