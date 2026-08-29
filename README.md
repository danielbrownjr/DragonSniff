# DragonSniff

**A local developer tool for sniffing out what Dragon-family devices are doing.**

DragonSniff exists because staring at browser DevTools, juggling diagnostic tabs, and muttering increasingly creative obscenities is not a sustainable observability strategy.

Connect to a Dragon-family device on your local network, inspect its API and event streams, watch health and memory behavior, exercise connection lifecycles, and bag a diagnostic session for later analysis.

> DragonSniff observes and exercises communications. It does not become part of the control loop. The dragon remains responsible for being a dragon.

## What DragonSniff is for

The first job is deliberately small: make interactive development and hardware validation less annoying without adding debugging machinery to production firmware every time something gets weird.

DragonSniff should eventually make it easy to:

- **Sniff out a Dragon: Device discovery** — connect by hostname or address and identify the device through the common Dragon HTTP API.
- **See what the Dragon is doing: Live state** — inspect `/api/v2/info`, `/api/v2/state`, and `/api/v2/health` without product-specific assumptions.
- **Follow the smoke: Event streams** — connect to `/api/v2/events`, timestamp SSE activity, and make connection state visible.
- **Poke it with a stick: Controlled communications testing** — deliberately connect, disconnect, reconnect, and exercise bounded SSE-client churn.
- **Watch its vital signs: Health history** — record uptime, boot identity, heap statistics, stack headroom, connection counts, and other truthful health fields exposed by the device.
- **Bag the evidence: Session export** — preserve timestamped raw payloads and connection events in a machine-readable diagnostic session for later analysis.

## The fence around the dragon pen

DragonSniff is **developer tooling**, not another Dragon product and not a control surface.

The initial project does **not** own or provide:

- heater, fan, motor, relay, or other actuator controls
- product settings editors
- PID tuning controls
- OTA or provisioning workflows
- cloud accounts or remote telemetry services
- safety policy
- MQTT infrastructure
- a replacement for automated host/HIL tests

If a feature would let DragonSniff become part of a device's safety or control boundary, it does not belong in the initial scope.

## Architecture

DragonSniff uses a small local host service between the browser and the device. The service binds only to `127.0.0.1`, makes the fixed read-only Dragon API requests, records their raw results, and serves the application UI. The browser never connects to the Dragon directly, so product firmware does not need developer-tool CORS behavior.

```text
Dragon device -- HTTP API and optional SSE --> local Python service --> browser UI
```

The runtime uses Python 3.11 or newer and only the standard library. Device traffic is bounded to two concurrent connections, local application requests are serialized, JSON responses are capped at 1 MiB, individual SSE events are capped at 256 KiB, and the in-memory session retains at most 2,000 records. These limits and current device-connection use are visible in the UI.

Only these device requests exist:

- `GET /api/v2/info`
- `GET /api/v2/state`
- `GET /api/v2/health`
- `GET /api/v2/events`

There is no generic device proxy and no device mutation route. Raw payloads are first-class evidence. Parsed views never discard fields that DragonSniff does not recognize.

## Run the first sniff

From a checkout with Python 3.11 or newer:

```console
python -m pip install -e .
dragonsniff
```

Open `http://127.0.0.1:8765`, enter an authorized local Dragon hostname or address, and start the session. You can also supply the initial target on the command line:

```console
dragonsniff --target dragonbreath.local
```

Use **Refresh JSON endpoints** for another serialized pass over info, state, and health. Use **Reconnect event stream** for one deliberate disconnect/reconnect. DragonSniff does not automatically retry a failed SSE stream in V1; that keeps failure evidence clear and avoids accidental churn. **Bag evidence as JSONL** downloads every retained lifecycle record in arrival order.

Run the tests without installing the package:

```console
PYTHONPATH=src python -m unittest discover -s tests -v
```

See [Dragon API findings](docs/dragon-api-findings.md) for the contracts observed in current firmware and the important distinction between common and optional behavior.

## Design rules

- Local-first. No cloud dependency is required to inspect a device on the bench.
- Observe before interpreting.
- Preserve raw evidence.
- Treat optional fields as optional.
- Keep network concurrency bounded and visible.
- Never silently mutate a device while observing it.
- Prefer common Dragon contracts over product-specific knowledge.
- Keep automated test harnesses deterministic; DragonSniff complements them rather than replacing them.
- Do not make Dragon firmware carry debugging complexity merely to make DragonSniff prettier.

## Here be dragons: security and safety

DragonSniff is intended for development networks and devices you are authorized to inspect. Device APIs remain responsible for authentication, authorization, validation, and safety enforcement. A browser or developer tool is never a safety boundary.

A diagnostic tool also changes the system it observes: HTTP requests consume sockets, heap, CPU time, and bandwidth. DragonSniff must make its own activity visible and avoid turning observation into an accidental denial-of-service test.

## Status

Issue #1 has a coherent local first vertical slice. It has automated fixture coverage but has not yet been exercised against physical Dragon hardware. The bounded churn runner from Issue #2 remains future work.

## Name

Yes, it is called **DragonSniff**.

No, we are not apologizing for that.
