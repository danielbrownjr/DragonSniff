# Changelog

## Unreleased

### Added

- Explicit bind, port, and log-level configuration for future headless/container use.
- A hardware-independent `/healthz` service-liveness endpoint.
- A versioned server and Docker readiness roadmap.
- Dragon-family navigation with separate Dashboard, Thermal, Churn, and Evidence surfaces plus the unlinked `/lab` display controls.
- Automatic pause and restoration of live observation around completed, cancelled, or failed automated runs.
- Dedicated Thermal and Churn JSONL downloads that remain available after observation resumes.
- Live retained-record budget feedback for passive-capture schedules.
- Bounded eight-worker local request handling so a slow automation transition does not freeze status polling.

### Fixed

- Clarify active mode and consequential stop actions across desktop and compact layouts.
- Handle SIGTERM with the same bounded session cleanup used by local shutdown.
- Keep the active timeline and global JSONL export on the same authoritative recorder after an automated run.
- Preserve a pending observation return across chained automated runs and prevent observer workers from starting after server shutdown.
- Retain the latest capture and churn evidence independently across later observation sessions and automated runs; unavailable run exports now return 404 instead of a zero-byte evidence file.
- Validate hash routes against owned page names, scope expert polling preferences to `/lab`, and use valid current-page navigation semantics.

### Validation

- Cover resumed exports, cancelled-run restoration, chained automation, shutdown races, route rejection, and capture-budget gating behavior.

## v0.2.0 — 2026-09-05

### Added

- Bounded passive thermal capture with Smoke, Soak, Extended, and Long Haul profiles.
- Independent state and health sampling schedules with raw JSONL evidence retention.
- Per-run recorder sizing so validated long-haul captures retain their opening records.
- Live chamber, PTC, target, PID demand, commanded duty, approach limit, and constraint telemetry.
- A compact real-time PID output gauge that degrades cleanly when optional fields are absent.
- Repeatable Baseline, Extended, and Stress profiles for bounded sequential SSE churn testing.

### Improved

- Monotonic per-fetch sequencing and consistent terminal capture counters.
- Post-churn settlement evidence and cleanup reporting without inventing crash or recovery causes.
- Validation coverage for capture scheduling, record budgets, telemetry extraction, and gauge rendering.

### Safety and scope

DragonSniff remains passive and loopback-only. This release adds no device mutations, actuator controls, generic proxying, OTA behavior, or unbounded capture mode.
