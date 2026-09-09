# Dragon API findings for DragonSniff V1

This historical note records what DragonSniff found in the ecosystem before choosing its V1 architecture. It describes the named source revisions below, not a current or frozen Dragon API specification.

## Sources inspected

- `justinh-rahb/dragon-core` `origin/main` at `4c04c7a1ffc5c61c31e66313af9515c45f6fba0c`
- `danielbrownjr/DragonBreath` main at `30e880b49c6385ef2b48202b6119bd8109c66dff` and current PID/UI feature at `bba937b768069de5400d87e4a4eba7791399fa06`
- `danielbrownjr/JumpJet` foundation feature at `9097b27b46544b26b5e66830b058071bb439edc3`

At the inspected revisions, dragon-core did not own product `/api/v2` response handlers. Its `dc_ui` component documented the browser-side expectation and treated `/api/v2/events` as optional, with state polling as a fallback. DragonBreath and Jump Jet owned their handlers.

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

Endpoint shape is product-specific. The inspected DragonBreath feature reported `api_version`, `boot_id`, uptime, heap information, Wi-Fi information, SSE-client information, and temporary validation diagnostics. The inspected Jump Jet foundation returned only `status: cold_safe` and `heater_available: false`.

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

The inspected feature branch contained additional SSE lifecycle diagnostics, but those did not define the general API contract. DragonSniff records arbitrary event names, IDs, comment-only transport blocks, data, parse failures, connection transitions, HTTP rejection bodies, and end-of-stream without assuming DragonBreath's event vocabulary. Comment-only blocks are retained as lifecycle evidence rather than dispatched or counted as application events.

## Error and availability handling

DragonBreath JSON error responses include product policy state in addition to an error code and message. An unavailable Jump Jet route follows its HTTP server's normal not-found behavior. Network failure, HTTP rejection, malformed JSON, missing routes, and clean SSE end-of-stream are distinct observations in DragonSniff's session.

Successfully UTF-8-decoded DUT text is retained exactly. Python's strict UTF-8
decoder cannot produce lone surrogates; the issue addressed here enters through
syntactically valid JSON escape sequences such as `\uD800` and `\uDC00`. After
JSON decoding, DragonSniff uses an explicit stack to validate every string,
including object keys and nested values, before admitting the parsed object to
structured state. The admission depth is capped at 128 so later bounded local
recording and serialization code never receives a recursively hazardous object.
Nesting beyond Python's JSON decoder limit is classified the same way rather than
allowing the decoder's `RecursionError` to escape the boundary.

`parse_error_kind` is `syntax`, `unsafe_text`, or `structure_too_deep` when
`parsed_available` is false for one of those reasons; it is `null` when parsing
and structured admission succeed. `parse_error` remains the human-readable
diagnostic. Structural positions use device-text-free components such as
`$/{object-value:0}/[1]`. DragonSniff does not normalize, replace, or invent a
corrected representation for malformed parsed Unicode.

If transport bytes are not valid UTF-8, `decode_error` records the failure,
`parsed_available` is false, and `raw_payload` is a safe replacement-decoded
view rather than byte-exact evidence. SSE evidence retains the first decode
failure in an event, bounding diagnostics even when multiple lines are invalid.
The replacement-decoded text is never admitted as parsed structured data.

For HTTP observations, `response_received` means an HTTP response was obtained,
`http_ok` means its status was 2xx, and `ok` means a 2xx response was also
available as structured data. Thus a reachable 200 response with unsafe text is
not usable (`ok: false`) but remains distinguishable from transport failure.
`response_too_large` separately marks a size failure. A truncated oversized
error body is retained only as a truncated textual view and is not parsed, so
its parse classification remains `null` rather than claiming a diagnosis from
non-authoritative content.

The live observer labels endpoint reachability from `http_ok`, while capture
success counters and churn-derived observations use `ok` because those consumers
require structured data. This prevents unsafe parsed text from making a reachable
200 endpoint look offline without admitting it to structured consumers.

DragonSniff does not silently substitute polling for SSE. It fetches `/state` during the initial JSON pass and permits explicit refreshes, while leaving the stream's unavailable or closed state visible. The bounded churn runner handles automated stream exercises separately.

## Architecture consequence

The products do not expose browser CORS headers as a common contract, and they should not need to. DragonSniff therefore uses its own host backend. It binds to loopback by default and supports an explicit exact-authority allowlist for trusted-LAN deployments, allowing a browser to inspect an authorized Dragon without adding developer-tool policy or allocations to firmware.

The backend has fixed read-only device routes, two device-connection permits, bounded bodies/events/session history, and no cloud or discovery behavior. The two permits are DragonSniff's own conservative resource budget and do not mirror or depend on DragonBreath's current two-client SSE cap. Its recorder and client lifecycle are separate from the UI, and the bounded churn runner reuses them without browser tabs.

SSE connection establishment is bounded to five seconds. Once established, a stream has no DragonSniff application-level inactivity timeout: SSE permits valid quiet streams, and DragonBreath's current two-second telemetry cadence is not assumed to be a family-wide contract. Explicit Stop or Reconnect closes the socket; transport failures remain recorded as errors unless the stream-specific stop condition is set. DragonSniff does not automatically reconnect.

The churn runner reuses these same fixed routes, recorder, parser, structured
admission rule, timeout semantics, and two-permit client budget. SSE event data
and JSON rejection bodies use the same admission rule. It opens at most one
churn-owned SSE connection at a time. The second permit allows a bounded health
sample while that stream is open; it is not a claim about device-side stream
capacity. Invalid SSE bytes carry their first `decode_error`; their replacement-
decoded textual view is not described as exact raw evidence.

Churn records HTTP 503 stream rejection without assuming every 503 has the same product cause. DragonBreath's current valid JSON `busy` response is preserved as one real-world example. Other HTTP statuses, invalid bodies, transport failures, remote EOF, deliberate disconnect, cancellation, and controller failures remain distinguishable evidence.
