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

Every observation, Thermal capture, and Churn run is then appended to its own JSONL file as records arrive. Complete records are flushed before the live view reports them. After an ungraceful stop, startup quarantines only an incomplete final JSONL record, derives authoritative counters from the retained evidence, marks a previously active session as `interrupted`, and never resumes device work automatically.

History distinguishes how a run ended: `completed` reached its normal boundary, `cancelled` received an orderly stop, `interrupted` was recovered after the process disappeared without a terminal transition, and `failed` records a known operational or persistence error. Recovery preserves every complete JSONL record—including a final request with no response—and never invents a response.

Storage defaults to 500 sessions and 256 MiB, with oldest finished sessions removed first. Active and currently downloading sessions are protected from retention. Use `--retention-sessions` and `--retention-bytes` to change those bounds. See the [server and container status](docs/server-docker-roadmap.md) for the detailed persistence and recovery contract.

`DRAGONSNIFF_DATA_DIR`, `DRAGONSNIFF_ALLOWED_TARGETS`, `DRAGONSNIFF_ALLOWED_HOSTS`, `DRAGONSNIFF_REQUIRE_ALLOWLIST`, `DRAGONSNIFF_RETENTION_SESSIONS`, and `DRAGONSNIFF_RETENTION_BYTES` provide environment equivalents. Comma-separate multiple environment allowlist entries. `--allow-host` may be repeated to add exact browser authorities; loopback on the listening port remains accepted by default.

`GET /healthz` reports whether the local web service is responsive and does not require device connectivity.

DragonSniff does not open a browser itself. SIGINT and SIGTERM both trigger a shared 12-second maximum for session and worker cleanup before the HTTP server closes. The supported container configuration allows 20 seconds so active request handlers also have time to leave their five-second socket bound.

### Docker Compose

Set the Dragon addresses the service may contact, then build and start it:

```powershell
$env:DRAGONSNIFF_ALLOWED_TARGETS = "http://192.0.2.40"
docker compose up --build -d
```

Open `http://127.0.0.1:8765`. Compose publishes only to host loopback, runs the application as a non-root user with a read-only container filesystem, and stores evidence in the `dragonsniff-data` volume. Direct IP addresses are generally more reliable than `.local` names across Docker Desktop networking.

The image itself listens on `0.0.0.0` inside its container. The official Compose mapping is deliberately `127.0.0.1:8765:8765`; a generic `docker run -p 8765:8765 ...` may expose DragonSniff beyond host loopback depending on Docker and host configuration. Host-header validation is a browser/network backstop, not authentication. Do not expose this unauthenticated developer service to an untrusted network.

A bind-mounted `/data` directory must be writable by the image's non-root `dragonsniff` user. Existing named volumes created by older images may also need their ownership corrected before this image can persist evidence.

Stop and restart without losing evidence:

```console
docker compose stop
docker compose start
```

Use `docker compose down` to remove the container while retaining the named volume. Adding `--volumes` intentionally removes stored evidence.

### Portainer / prebuilt image

Main-branch images are published publicly as `ghcr.io/danielbrownjr/dragonsniff:latest` and as an immutable `sha-<full-commit-sha>` tag. `latest` is convenient for routine upgrades; the SHA tag gives a reproducible deployment and rollback point. Portainer should use `image:`, not a remote `build:` context, so neither Git nor a local image build is required on the NAS.

See the [direct trusted-LAN Portainer recipe](docs/portainer.md) for the hardware-validated one-service stack with persistent evidence. A reference NAS deployment publishes DragonSniff directly with its configured authority. The package is public, so do not configure a Portainer registry token solely to pull DragonSniff.

## Concepts

- **Observer:** fetches the three JSON endpoints and holds one SSE stream. Stop/reconnect controls affect the stream without silently replacing the session.
- **Capture:** polls fixed state and health endpoints on a bounded schedule. It pauses live observation and restores it after cleanup.
- **Churn:** performs bounded, sequential SSE lifecycle exercises. Capacity rejection and cleanup timing are retained as evidence.
- **Session recorder:** stores ordered raw and parsed observations in bounded memory and, when configured, appends them to durable JSONL evidence.

Only one operating mode is active at a time. The UI identifies the active mode, whether it is running/stopping/complete, and which evidence export is available. Completed Thermal and Churn evidence remains separately downloadable after observation resumes; the global export follows the session currently shown.

## Exporting evidence

The dashboard offers **Download current session JSONL** only while the current session owns recorded evidence; otherwise it says **No current evidence**. Thermal and Churn provide run-specific downloads once their evidence exists. **History** is the durable, authoritative source for prior persisted sessions. JSONL records retain timestamps, request identities, raw response bodies, parsed JSON when valid, SSE lifecycle events, and cleanup outcomes.

Downloads use stable names that identify their ownership: `dragonsniff-session.jsonl` for the active session, `dragonsniff-thermal-capture.jsonl` for a retained Thermal run, and `dragonsniff-sse-churn.jsonl` for a retained Churn run.

The active-run downloads remain available. With persistent storage enabled, **History** also lists independently downloadable observation, Thermal, and Churn sessions after a process or container restart.

## Documentation

- [Getting started and concepts](https://github.com/danielbrownjr/DragonSniff/wiki)
- [Passive thermal capture](docs/thermal-capture.md)
- [Bounded SSE churn runner](docs/churn-runner.md)
- [Dragon API findings](docs/dragon-api-findings.md)
- [Hardware validation](docs/hardware-validation.md)
- [Portainer trusted-LAN deployment](docs/portainer.md)
- [Server and container status](docs/server-docker-roadmap.md)

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

DragonSniff is developer tooling at version 0.2.0. Live observation, bounded SSE churn, passive thermal capture, durable JSONL evidence, restart recovery, bounded retention, local Docker Compose, and direct trusted-LAN Portainer deployment are implemented and validated. Authentication, HTTPS, and public or untrusted-network operation are not.

The tool does not provide actuator controls, settings editing, PID tuning, OTA, provisioning, cloud telemetry, or safety policy. Device firmware remains responsible for authentication, validation, interlocks, and safe behavior.

HTTPS deployment and the remaining Brave download warning are tracked in [Issue #26](https://github.com/danielbrownjr/DragonSniff/issues/26). Current and deferred container work is summarized in the [server and container status](docs/server-docker-roadmap.md).

> **Is this scope creep? Yes. Anyway.**

## Name

Yes, it is called **DragonSniff**. No, we are not apologizing for that.
