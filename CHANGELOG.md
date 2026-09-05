# Changelog

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
