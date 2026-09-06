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

DragonSniff currently writes no application data to disk. Observation, capture, and churn evidence lives in bounded in-memory `SessionRecorder` instances until the browser downloads JSONL.

| Data | Current lifetime | Future classification |
|---|---|---|
| Active observation records | Process memory | Persistent session data |
| Completed capture/churn records | Process memory | Persistent session data |
| JSONL downloads | Browser-selected location | Export, not service state |
| Logs | Standard output/error | Container log stream |
| Static assets | Installed package | Read-only image content |

There is no cache, temporary-file, configuration-file, or generated-report directory today. Adding an unused data-directory option would suggest persistence that does not exist, so this pass intentionally does not add one.

## Blocking work before a supported Docker service

1. **Incremental evidence persistence.** Define an append-safe session format and write records as they arrive rather than only at download time.
2. **Interrupted-run recovery.** On startup, classify an unfinished session truthfully and keep its evidence exportable.
3. **Retention.** Bound stored evidence by age and/or total size before unattended operation.
4. **Historical sessions.** Add a read-only session index and downloads without letting stale completed work replace the active session.
5. **Target allowlist.** Keep the four fixed device paths and add an explicit set of permitted Dragon targets for an always-on service.
6. **Container validation.** Only then add and exercise a non-root image, Compose example, volume, healthcheck, restart behavior, and stop/restart tests.

Until those items exist, a Dockerfile would package an ephemeral process while implying persistence and recovery that DragonSniff cannot provide.

## Intended container boundary

The first supported deployment remains host-local:

```text
browser -> 127.0.0.1:8765 on host -> container 0.0.0.0:8765 -> authorized Dragon device
```

The Compose mapping should be `127.0.0.1:8765:8765`, not a LAN-wide publish. `0.0.0.0` is for the container namespace only. LAN or multi-user access requires a separate authentication, authorization, CSRF, and threat-model decision.

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
- SIGTERM completes bounded cleanup and flushes evidence before exit.
- Stop/restart does not corrupt JSONL or silently resume device work.
- UI/API/session/export tests pass inside and outside the container.

Track implementation in [Issue #7](https://github.com/danielbrownjr/DragonSniff/issues/7).
