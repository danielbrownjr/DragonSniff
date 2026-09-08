# Changelog

## v0.4.0 — 2026-09-08

### Added

- Durable, idempotent operator annotations for active Thermal captures, including quick-pick markers, exact freeform notes, capture-relative and UTC timing, and optional external-instrument correlation metadata.
- Local retry resolution for interrupted annotation submissions without duplicate evidence or DUT-facing mutation traffic.

### Validation

- Exercise a genuine lost client response on the NAS after the annotation was durably stored, restart DragonSniff, and confirm that retrying the same UUID and logical content returns the original record with `created: false` and no duplicate.
- Confirm annotations remain ordered with telemetry, survive reload, reconnect, graceful restart, and interrupted-capture recovery, and export without content or identity loss.
- Confirm the observed-device request audit remains GET-only and annotation capacity neither evicts nor corrupts telemetry evidence.

### Known limitations

- Issue #33 remains open for device-originated lone-surrogate serialization hardening. Operator-input Unicode validation is implemented; malformed Unicode originating from a device is separate and is not claimed fixed here.
- Authentication, HTTPS, and public or untrusted-network deployment remain unsupported.

## v0.3.0 — 2026-09-08

### Added

- Explicit bind, port, and log-level configuration for headless/container use.
- A hardware-independent `/healthz` service-liveness endpoint.
- A versioned server/container status and remaining-work reference.
- Distinct filenames for active-session, Thermal-capture, and SSE-churn exports.
- Dragon-family navigation with separate Dashboard, Thermal, Churn, and Evidence surfaces plus the unlinked `/lab` display controls.
- Automatic pause and restoration of live observation around completed, cancelled, or failed automated runs.
- Dedicated Thermal and Churn JSONL downloads that remain available after observation resumes.
- Live retained-record budget feedback for passive-capture schedules.
- Bounded eight-worker local request handling so a slow automation transition does not freeze status polling.
- Incremental append-only evidence persistence with interrupted-run recovery and bounded retention.
- Read-only session history API, UI, and purpose-specific historical JSONL downloads.
- Exact normalized target allowlisting for unattended service operation.
- A non-root Docker image and host-loopback Compose deployment with persistent storage and health checks.
- Exact trusted browser authorities for direct trusted-LAN deployment without rewriting `Host` or `Origin` headers.
- Public GHCR images tagged as `latest` and immutable full commit SHAs.
- A direct, hardened Portainer deployment guide using the published image.
- GitHub funding metadata for the project's Ko-fi page.

### Fixed

- Clarify active mode and consequential stop actions across desktop and compact layouts.
- Handle SIGTERM with the same bounded session cleanup used by local shutdown.
- Keep the active timeline and global JSONL export on the same authoritative recorder after an automated run.
- Preserve a pending observation return across chained automated runs and prevent observer workers from starting after server shutdown.
- Retain the latest capture and churn evidence independently across later observation sessions and automated runs; unavailable run exports now return 404 instead of a zero-byte evidence file.
- Validate hash routes against owned page names, scope expert polling preferences to `/lab`, and use valid current-page navigation semantics.
- Stream historical downloads from a fixed initial file length instead of allocating the complete evidence file in memory.
- Return `404 session_evidence_not_available` instead of a zero-byte current-session download when no current evidence exists.
- Preserve every complete record, including a final unmatched request, when startup classifies an unfinished run as `interrupted`.
- Record malformed HTTP status lines and other HTTP protocol failures as truthful failed samples without aborting a bounded capture or inventing a response.

### Validation

- Cover resumed exports, cancelled-run restoration, chained automation, shutdown races, route rejection, and capture-budget gating behavior.
- Cover durable write ordering, crash-window metadata reconciliation, partial-record quarantine, retention, allowlisting, history downloads, and container security defaults.
- Keep package metadata, the exported module version, and HTTP client/server identifiers on one authoritative version source.

### Known limitations

- HTTPS deployment is not yet supported, and Brave may warn about JSONL downloads over plain LAN HTTP; this remains tracked in Issue #26.

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
