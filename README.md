# DragonSniff

**Local, read-only observability tooling for Dragon-family devices.**

DragonSniff gives firmware developers one place to inspect Dragon HTTP APIs, follow event streams, run bounded communications exercises, and export timestamped evidence. It observes and records; it never becomes part of a device's control or safety loop.

> The dragon remains responsible for being a dragon.

## What it does

DragonSniff connects to one authorized device and exposes five focused browser surfaces:

| Surface | Purpose |
|---|---|
| **Dashboard** | Start or stop live observation and see connection/session state. |
| **Thermal** | Run a bounded passive state/health capture with live thermal context. |
| **Churn** | Exercise sequential SSE connect/observe/disconnect lifecycles. |
| **History** | Review and download durable completed or interrupted sessions. |
| **Evidence** | Inspect raw and parsed responses, event history, and exports. |

An unlinked `/lab` route contains display-only expert options. Hidden does not mean authenticated; the network boundary remains authoritative.

DragonSniff makes only these device requests:

- `GET /api/v2/info`
- `GET /api/v2/state`
- `GET /api/v2/health`
- `GET /api/v2/events`

There is no generic proxy and no device mutation route.

## Requirements and support

- Python 3.11 or newer
- A modern browser
- Network access to a Dragon-family device you are authorized to inspect

The application has no runtime dependencies outside the Python standard library. Windows is used for current physical validation and CI runs on Linux. Other Python 3.11+ environments are expected to work but are not yet physically qualified.

## Install and run

From a repository checkout:

```console
python -m pip install -e .
dragonsniff
```

Open `http://127.0.0.1:8765`, enter a Dragon hostname, address, or HTTP(S) origin, and choose **Start session**. The initial target and port may also be supplied on the command line:

```console
dragonsniff --target dragonbreath.local --port 8765
```

Do not open `src/dragonsniff/web/index.html` directly. The browser UI depends on the local DragonSniff service.

### Persistent local service

Local operation remains the safe default:

```console
dragonsniff --bind 127.0.0.1 --port 8765 --log-level INFO
```

The same values may be supplied as `DRAGONSNIFF_BIND`, `DRAGONSNIFF_PORT`, and `DRAGONSNIFF_LOG_LEVEL`. Add a data directory and explicit device allowlist for durable unattended use:

```console
dragonsniff --data-dir ./dragonsniff-data --allow-target dragonbreath.local --require-allowlist
```

Every observation, Thermal capture, and Churn run is then appended to its own JSONL file as records arrive. Startup marks a previously active session as `interrupted`; it remains downloadable and is never silently resumed. Storage defaults to at most 500 sessions and 256 MiB, with oldest finished sessions removed first. Active sessions are never removed by retention.

Use `--retention-sessions` and `--retention-bytes` to change those bounds. `DRAGONSNIFF_DATA_DIR`, `DRAGONSNIFF_ALLOWED_TARGETS`, `DRAGONSNIFF_REQUIRE_ALLOWLIST`, `DRAGONSNIFF_RETENTION_SESSIONS`, and `DRAGONSNIFF_RETENTION_BYTES` provide the equivalent environment configuration. Comma-separate multiple environment allowlist entries.

`GET /healthz` reports whether the local web service is responsive and does not require device connectivity.

DragonSniff does not open a browser itself. SIGINT and SIGTERM both trigger bounded session cleanup before the server closes.

### Docker Compose

Set the Dragon addresses the service may contact, then build and start it:

```powershell
$env:DRAGONSNIFF_ALLOWED_TARGETS = "http://192.0.2.40"
docker compose up --build -d
```

Open `http://127.0.0.1:8765`. Compose publishes only to host loopback, runs the application as a non-root user with a read-only container filesystem, and stores evidence in the `dragonsniff-data` volume. Direct IP addresses are generally more reliable than `.local` names across Docker Desktop networking.

Stop and restart without losing evidence:

```console
docker compose stop
docker compose start
```

Use `docker compose down` to remove the container while retaining the named volume. Adding `--volumes` intentionally removes stored evidence.

## Concepts

- **Observer:** fetches the three JSON endpoints and holds one SSE stream. Stop/reconnect controls affect the stream without silently replacing the session.
- **Capture:** polls fixed state and health endpoints on a bounded schedule. It pauses live observation and restores it after cleanup.
- **Churn:** performs bounded, sequential SSE lifecycle exercises. Capacity rejection and cleanup timing are retained as evidence.
- **Session recorder:** stores ordered raw and parsed observations in bounded memory and, when configured, appends them to durable JSONL evidence.

Only one operating mode is active at a time. The UI identifies the active mode, whether it is running/stopping/complete, and which evidence export is available. Completed Thermal and Churn evidence remains separately downloadable after observation resumes; the global export follows the session currently shown.

## Exporting evidence

Use **Bag evidence as JSONL** for the active session. Thermal and Churn provide run-specific downloads once their evidence exists. JSONL records retain timestamps, request identities, raw response bodies, parsed JSON when valid, SSE lifecycle events, and cleanup outcomes.

Downloads use stable names that identify their ownership: `dragonsniff-session.jsonl` for the active session, `dragonsniff-thermal-capture.jsonl` for a retained Thermal run, and `dragonsniff-sse-churn.jsonl` for a retained Churn run.

The active-run downloads remain available. With persistent storage enabled, **History** also lists independently downloadable observation, Thermal, and Churn sessions after a process or container restart.

## Documentation

- [Getting started and concepts](https://github.com/danielbrownjr/DragonSniff/wiki)
- [Passive thermal capture](docs/thermal-capture.md)
- [Bounded SSE churn runner](docs/churn-runner.md)
- [Dragon API findings](docs/dragon-api-findings.md)
- [Hardware validation](docs/hardware-validation.md)
- [Server and Docker roadmap](docs/server-docker-roadmap.md)

## Development

Run the checked-out source and all tests without installing it:

```console
PYTHONPATH=src python -m unittest discover -s tests -v
node --check src/dragonsniff/web/payload.js
node --check src/dragonsniff/web/app.js
node --test tests/payload.test.cjs
```

PowerShell equivalent:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## Status and boundaries

DragonSniff is developer tooling at version 0.2.0. Live observation, bounded SSE churn, passive thermal capture, durable JSONL evidence, restart recovery, bounded retention, and a host-local Docker deployment are implemented. Authentication and remote multi-user operation are not.

The tool does not provide actuator controls, settings editing, PID tuning, OTA, provisioning, cloud telemetry, or safety policy. Device firmware remains responsible for authentication, validation, interlocks, and safe behavior.

See [Issue #7](https://github.com/danielbrownjr/DragonSniff/issues/7) for the persistent Docker-daemon milestone.

> **Is this scope creep? Yes. Anyway.**

## Name

Yes, it is called **DragonSniff**. No, we are not apologizing for that.
