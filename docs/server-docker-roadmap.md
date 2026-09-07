# Server and container status

DragonSniff is a persistent, headless-capable service today. This document records the shipped server and container contract and separates it from the remaining work. The README is the primary entry point; the [Portainer guide](portainer.md) is the authoritative trusted-LAN deployment recipe.

## Shipped runtime

- One Python process serves the browser UI and local JSON actions.
- The default listener is `127.0.0.1:8765`.
- `--bind`, `--port`, and `--log-level` have matching `DRAGONSNIFF_*` environment variables.
- `--bind 0.0.0.0` is an explicit container mode. It does not infer trusted browser authorities.
- `GET /healthz` reports local service availability without requiring a Dragon device.
- SIGINT and SIGTERM share one bounded session-cleanup path before server close.
- Static assets load from the installed package; no browser or interactive terminal is required after startup.
- Local request workers and device connections have fixed limits.

## Persistence and recovery

With `--data-dir`, each observation, Thermal capture, and Churn run owns a session directory containing `metadata.json` and append-only `evidence.jsonl`. Without it, bounded in-memory operation remains available for short interactive use.

Each complete JSONL record is flushed before the live recorder reports success. Metadata is cached, checkpointed every 64 records, and synchronously updated on terminal transitions. Startup streams unfinished evidence to reconcile counters, quarantines only an incomplete final record as `evidence.partial`, and marks a previously active run `interrupted`. It never resumes device work or invents a response.

History keeps `completed`, `cancelled`, `failed`, and `interrupted` distinct. Retention is bounded by total bytes and session count, protects active and leased downloads, accounts for invalid session directories, and treats deletion failure as retryable housekeeping. A top-level storage traversal failure produces a bounded JSON error rather than a false empty result.

Session/evidence creation and atomic metadata replacement flush their containing directories on platforms that expose directory `fsync`. Windows retains atomic replacement and file-flush guarantees without claiming portable directory-flush behavior. New Windows evidence uses binary mode; recovery recognizes legacy CRLF evidence sizes.

## Security and network boundary

DragonSniff makes only fixed read-only requests to authorized Dragon targets. It is not a generic proxy and exposes no device mutation route.

- Localhost and `127.0.0.1` on the listening port are trusted browser authorities by default.
- `DRAGONSNIFF_ALLOWED_HOSTS` or repeated `--allow-host` values add exact trusted authorities.
- Wildcards, malformed authorities, unconfigured LAN addresses, and wrong ports remain rejected.
- Browser POST requests with an `Origin` header require its authority to match the accepted `Host`.
- A missing Origin remains supported for non-browser clients.
- Host/Origin validation is a backstop, not authentication. Do not expose DragonSniff to the public internet or another untrusted network.

## Supported deployment topologies

### Local Compose

The repository Compose file builds locally, publishes `127.0.0.1:8765:8765`, runs non-root with a read-only root filesystem and dropped capabilities, and persists `/data` in a named volume.

```text
browser -> 127.0.0.1:8765 -> DragonSniff container:8765 -> authorized Dragon
```

### Trusted-LAN Portainer

The public GHCR image supports a direct one-service deployment with an explicit external browser authority:

```text
browser -> NAS:published-port -> DragonSniff container:8765 -> authorized Dragon
```

DragonSniff now validates the real NAS authority directly. The one-service topology has passed a live no-Caddy smoke test, and the former Caddy header-rewrite shim has been retired. See the [Portainer guide](portainer.md) for the complete stack and validation details.

The published runtime platform is currently Linux/amd64. The image is available as `latest` and an immutable `sha-<full-commit-sha>` tag.

## Device connectivity

DragonSniff talks to Dragons over HTTP(S), not host USB or serial devices. Containers therefore need ordinary network reachability to the target. Direct IP addresses are the most predictable option; `.local`/mDNS resolution may not cross desktop or bridged-container boundaries reliably.

## Remaining work

- [Issue #10](https://github.com/danielbrownjr/DragonSniff/issues/10): characterize the remaining stale-SSE-write/HTTP-connection corruption report without conflating it with eventual resource cleanup.
- [Issue #26](https://github.com/danielbrownjr/DragonSniff/issues/26): define a supported HTTPS deployment and resolve or explain the Brave warning for LAN evidence downloads.
- Add other runtime platforms only after their build and deployment behavior is validated.
- Authentication and remote multi-user operation remain explicitly deferred; trusted-host configuration does not provide either.

No current roadmap item authorizes device mutation, a generic proxy, weaker target allowlisting, or public-internet exposure.
