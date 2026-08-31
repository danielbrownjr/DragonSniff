# Bounded SSE churn runner

DragonSniff's churn runner deliberately exercises one read-only communications lifecycle: `GET /api/v2/events`. It exists to replace manual piles of browser tabs with a deterministic, bounded, exportable sequence. It is developer instrumentation, not a generic load generator or product controller.

## Lifecycle

Each run receives a unique run ID. Cycles are sequential and carry their cycle number into every HTTP, SSE, health, and controller record:

1. Sample `/api/v2/health` before the run when available.
2. Attempt one `/api/v2/events` connection using the five-second establishment timeout.
3. If it opens, sample health while the stream is held.
4. Observe until either the duration bound or application-event bound is reached.
5. Deliberately close the stream, join its worker, and verify the local connection permit returned.
6. Sample health after disconnect. Rejected or failed attempts receive an after-attempt sample instead.
7. Wait the configured nonzero delay, then begin the next cycle.
8. Sample immediate final health.
9. Enter bounded settlement for completed or cancelled runs. Sample health after one, two, five, and ten seconds, stopping early when an optional SSE-client count returns to its pre-run baseline.
10. Record terminal cleanup after the local workers and connection permits are clean.

Comment-only SSE blocks are retained as `sse_comment` transport evidence but do not satisfy the application-event bound. Remote EOF is different from deliberate disconnect. HTTP rejection is different from transport failure. A local connection-budget failure is different from both.

## Hard bounds

| Input | Default | Minimum | Maximum |
|---|---:|---:|---:|
| Cycles | 3 | 1 | 20 |
| Observation seconds | 2.0 | 0.25 | 15.0 |
| Application events per cycle | 3 | 1 | 25 |
| Delay seconds | 0.5 | 0.1 | 5.0 |

The runner has no infinite mode and no concurrency option. It owns at most one SSE stream. Its `DragonClient` still has the existing two-permit local budget so a health request can be sampled while that stream is open. Normal observation and churn are mutually exclusive and the backend enforces that boundary.

## Evidence and export

The existing bounded `SessionRecorder` and JSONL export are reused. Raw payloads and unknown fields are not normalized away. Evidence includes:

- run start, configuration, target, bounds, and run ID
- cycle start and finish summaries
- correlated request and connection IDs
- connection timing and response headers
- raw and parsed capacity-rejection bodies
- SSE open, event, comment, ignored block, error, and close records
- deliberate-disconnect reason
- health sample point, status, raw body, parsed body, and optional observations
- boot-ID changes with previous/current values and observation point
- cancellation or controller failure
- worker, permit, and terminal cleanup outcome

The compact copy action is derived from the stored run state. The raw-health copy action uses the stored raw payload, not text scraped from the rendered page.

## Capacity and slot interpretation

A stream-capacity rejection is a valid observation. DragonBreath currently uses HTTP 503 with a JSON `busy` body when both of its slots are occupied. DragonSniff records that behavior but does not encode two slots as a family contract, retry indefinitely, raise concurrency, or change the device.

Optional client counts, connection IDs, socket descriptors, and related diagnostics are preserved when exposed. A file descriptor may be reused. Connection IDs may change. Other tools or browser tabs may own clients. None of those facts alone proves a leak or stale slot. Slot-reclamation conclusions require a sequence of health and lifecycle evidence that actually supports them.

## Health and reboot evidence

The runner never requires product-specific health fields. It may surface `boot_id`, uptime, heap values, task headroom, `sse_clients`, or connection diagnostics when present. Missing `/api/v2/health`, missing fields, or new unknown fields do not fail the run.

If a nonempty boot identifier changes, DragonSniff records the old value, new value, cycle, sample point, and time. It reports only that the identifier changed. Reboot, watchdog reset, OTA, manual power cycling, or another cause cannot be inferred from that field alone.

## Post-run settlement

Immediate post-disconnect samples are deliberately preserved because they reveal whether a short inter-cycle delay can outrun device-side SSE reclamation. DragonSniff does not insert a long pause between cycles or mask a capacity rejection.

After the connection phase, completed and cancelled runs enter one bounded settlement phase. Health is sampled at absolute checkpoints of one, two, five, and ten seconds. If both the pre-run and current health payloads contain a nonnegative integer `sse_clients`, settlement ends early when the current value is less than or equal to the pre-run value. Comparison is baseline-relative rather than zero-relative because another authorized client may own a stream. A delayed heap value is retained alongside each settlement sample when exposed.

The SSE-client field remains optional and product-specific. If it is absent but a heap measurement exists, DragonSniff takes one delayed sample and reports that client recovery is not comparable. If neither signal exists, settlement is immediately marked not applicable. If a comparable count remains above baseline at ten seconds, settlement is recorded as timed out evidence without turning an otherwise completed run into a failure. Raw health payloads and all settlement lifecycle records remain in the JSONL export.

## Cancellation and cleanup

Cancellation stops future cycles, signals the active stream-specific stop event, shuts down and closes the owned stream, and boundedly joins the controller and stream worker. Evidence collected before cancellation remains exportable. After local stream cleanup it uses the same bounded settlement phase as a completed run, so cancellation can capture eventual device-visible recovery without scheduling another SSE cycle. The run becomes `cancelled` only after settlement and local cleanup are complete.

An internal controller exception is recorded as `churn_internal_failure`. It does not escape as the only worker-thread traceback. Cleanup timeouts are evidence and prevent a false completed/cancelled cleanup claim until the remaining resource actually exits.

## Safety and security boundary

The new local actions inherit DragonSniff's loopback binding, Host allowlist, same-origin POST policy, JSON media-type requirement, bounded request body, and lack of CORS. All configuration is validated server-side.

Device traffic remains limited to fixed read-only GETs for `/api/v2/events` and `/api/v2/health` during churn. No arbitrary path, method, body, discovery, actuator control, settings mutation, provisioning, OTA, MQTT, or product safety behavior is available.
