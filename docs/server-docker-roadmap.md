# Server and Docker roadmap

DragonSniff already separates its browser UI from device communication, serves packaged static assets, avoids desktop GUI dependencies, and bounds its local request and device-connection workers. Those traits make it a useful headless service today. They do not yet make it a persistent Docker daemon.

## Current runtime model

- One Python process serves the UI and local JSON actions.
- The default listener is `127.0.0.1:8765`.
- `--bind`, `--port`, and `--log-level` have matching `DRAGONSNIFF_*` environment variables.
- `--bind 0.0.0.0` is an explicit container-preparation mode; browser Host validation remains limited to `127.0.0.1` and `localhost`.
- `GET /healthz` reports local service availability without requiring a Dragon device.
- SIGINT and SIGTERM run the existing bounded session cleanup before server close.
- Static assets resolve from the installed package rather than the current directory.
- No browser or interactive terminal is required after startup.

## Runtime-written data

When `--data-dir` is configured, DragonSniff incrementally appends observation, capture, and churn evidence to independent session directories. Without it, the original bounded in-memory behavior remains available for short interactive use.

| Data | Current lifetime | Future classification |
|---|---|---|
| Active observation records | Bounded memory + optional JSONL | Persistent session data |
| Completed capture/churn records | Bounded memory + optional JSONL | Persistent session data |
| JSONL downloads | Browser-selected location | Export, not service state |
| Logs | Standard output/error | Container log stream |
| Static assets | Installed package | Read-only image content |

Persistent mode uses `<data-dir>/sessions/<session-id>/metadata.json` plus `evidence.jsonl`. Each JSONL record is appended and `fsync`ed before the live recorder reports success; that valid JSONL prefix is the authoritative evidence. Metadata is cached, checkpointed every 64 records, and synchronously flushed for terminal state. Startup streams active evidence to reconcile counters and uses bounded reverse reads to find and quarantine an incomplete final record as `evidence.partial`. A known append failure is durably classified as `failed` when the filesystem still permits the terminal metadata write; if no further write is possible, the valid JSONL prefix remains recoverable but the last on-disk metadata state is necessarily the limit of what can be guaranteed.

Session/evidence creation and atomic metadata replacement also flush their containing directories on platforms that expose directory `fsync`. Windows does not provide that operation through Python's portable file-descriptor API, so DragonSniff retains atomic replace and file flush guarantees there without claiming a directory-flush guarantee. Retention leases active downloads and treats deletion failure as retryable housekeeping rather than failing a live run.

A canonical session directory whose metadata is malformed, unreadable, or from an unsupported format version is invalid for normal History and download semantics. It is not hidden from storage accounting: its logical file bytes and one session-count slot remain in the retention budget and in the History API's storage summary. Invalid sessions are eligible for retention removal, using directory modification time only as a fallback ordering value rather than treating it as a trustworthy session creation time.

## Implemented Docker-service foundation

1. **Incremental evidence persistence.** Records are append-only JSONL and remain bounded in live memory.
2. **Interrupted-run recovery.** Startup truthfully classifies unfinished sessions without resuming device work.
3. **Retention.** Stored evidence is bounded by both total bytes and session count.
4. **Historical sessions.** Read-only API/UI history and downloads remain separate from active state.
5. **Target allowlist.** Exact normalized Dragon origins may be explicitly permitted; the container requires at least one.
6. **Container boundary.** The image is non-root and Compose supplies a volume, loopback-only publish, healthcheck, restart policy, read-only root filesystem, and dropped capabilities.

Image build and real container stop/restart validation still require a host with Docker available. The normal host and browser suites validate the underlying persistence, recovery, history, and allowlist behavior without Docker.

## Intended container boundary

The first supported deployment remains host-local:

```text
browser -> 127.0.0.1:8765 on host -> container 0.0.0.0:8765 -> authorized Dragon device
```

The image listens on `0.0.0.0` inside its container. The Compose mapping is `127.0.0.1:8765:8765`, not a LAN-wide publish. Running the image with a generic `docker run -p 8765:8765 ...` may publish it beyond loopback depending on Docker and host configuration. Host validation is a browser/network backstop, not authentication. LAN or multi-user access requires a separate authentication, authorization, CSRF, and threat-model decision.

The mounted `/data` path must be writable by the image's non-root user. A pre-existing bind mount or named volume created with different ownership may require an operator to correct that ownership before starting the service.

The service does not enable CORS. Browser actions must retain matching Host and Origin checks, and the application must remain a fixed read-only Dragon client rather than a generic network proxy.

## Device connectivity

DragonSniff currently talks to Dragons over HTTP(S), not host USB or serial devices. Container deployments therefore need ordinary LAN reachability to the target.

- Direct IP addresses are the most predictable option.
- `.local`/mDNS resolution may not cross Docker Desktop or bridged-network boundaries reliably.
- Host networking is platform-specific and should not be the default merely to make discovery convenient.
- If USB/serial support is ever added, device passthrough and permissions must remain transport configuration, not hard-coded `/dev/tty*` or Windows paths.

## Acceptance criteria for the Docker milestone

- Image builds reproducibly and runs as a non-root user.
- Compose publishes only to host loopback by default.
- `/healthz` passes without a Dragon connected.
- A mounted data directory receives incremental evidence and no source-tree writes occur.
- Capture and churn evidence survives browser closure and container restart.
- Interrupted runs are marked, retained, and downloadable after restart.
- SIGTERM gives session/worker cleanup one shared 12-second deadline; the supported Compose deployment provides a 20-second grace period to include bounded HTTP handler shutdown.
- Stop/restart does not corrupt JSONL or silently resume device work.
- UI/API/session/export tests pass inside and outside the container.

Track implementation in [Issue #7](https://github.com/danielbrownjr/DragonSniff/issues/7).
