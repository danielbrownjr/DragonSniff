# DragonSniff 🐉👃

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

## Architecture direction

DragonSniff should consume the public/common Dragon API rather than reach into firmware implementation details.

```text
DragonSniff
    |
    +-- Dragon API v2
    |     +-- /api/v2/info
    |     +-- /api/v2/state
    |     +-- /api/v2/health
    |     +-- /api/v2/events
    |
    +-- Recorder
    |     +-- timestamps
    |     +-- raw payloads
    |     +-- connection lifecycle
    |     +-- derived timing
    |     +-- export
    |
    +-- Optional capability adapters
          +-- generic Dragon
          +-- DragonBreath
          +-- Jump Jet
```

Raw payloads are first-class evidence. A field DragonSniff does not understand should remain visible and recordable rather than being silently discarded.

Product-specific adapters may improve presentation, but the generic observer must remain useful when talking to a future Dragon it has never met before.

## First sniff

The first useful milestone is intentionally boring:

1. Connect to one explicitly supplied local Dragon device.
2. Fetch and display info, state, and health.
3. Open and visibly monitor the SSE stream.
4. Record timestamped request/event/connection history.
5. Export the session.
6. Exercise a small, bounded SSE connect/disconnect/reconnect test.

No automatic network scanning is required for the first milestone. No control mutations are required at all.

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

🌱 **Very early.** The project charter exists; implementation choices are intentionally not frozen yet.

The immediate use case that inspired DragonSniff is repeatable observation of Dragon-family HTTP/SSE behavior during hardware validation. That is a useful first target, not permission to grow a dragon-sized monitoring platform before the basics work.

## Name

Yes, it is called **DragonSniff**.

No, we are not apologizing for that.
