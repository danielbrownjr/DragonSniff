# Portainer trusted-LAN deployment

DragonSniff can run directly on a trusted LAN from its public GHCR image. This deployment is a single DragonSniff service: no Git checkout, NAS-side build, registry credential, or header-rewriting proxy is required.

A reference NAS deployment has validated this direct topology at an external published port.

DragonSniff is an unauthenticated developer service. Use this recipe only on a trusted LAN, never port-forward it to the internet, and allow only Dragon devices you are authorized to inspect. Host validation is a browser/network backstop, not authentication. The browser is not a safety boundary; DragonSniff itself remains read-only to device APIs.

## Stack

Replace the two placeholder addresses before deploying:

- `DRAGONSNIFF_ALLOWED_TARGETS` is the Dragon device address.
- `DRAGONSNIFF_ALLOWED_HOSTS` is the exact address and external port the browser uses to reach the NAS.

```yaml
services:
  dragonsniff:
    image: ghcr.io/danielbrownjr/dragonsniff:latest
    init: true
    restart: unless-stopped
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    tmpfs:
      - /tmp
    ports:
      - "8766:8765"
    environment:
      DRAGONSNIFF_ALLOWED_TARGETS: "DRAGON_IP"
      DRAGONSNIFF_ALLOWED_HOSTS: "NAS_IP:8766"
      DRAGONSNIFF_LOG_LEVEL: "INFO"
      DRAGONSNIFF_RETENTION_BYTES: "268435456"
      DRAGONSNIFF_RETENTION_SESSIONS: "500"
    volumes:
      - dragonsniff-data:/data
    stop_grace_period: 20s

volumes:
  dragonsniff-data:
```

The mapping `8766:8765` means **host port 8766 is not container port 8765**. The browser opens `http://NAS_IP:8766`; DragonSniff still listens on port 8765 inside the container. Choose another external port if needed and put that same external authority in `DRAGONSNIFF_ALLOWED_HOSTS`.

Multiple exact browser authorities may be comma-separated. DragonSniff also accepts `localhost:<container-port>` and `127.0.0.1:<container-port>` by default. It does not accept wildcards, unconfigured LAN addresses, or a different port on an otherwise accepted host. A browser POST with an Origin header must use the same authority as its accepted Host header.

The image healthcheck remains `GET http://127.0.0.1:8765/healthz` inside the container. The named `dragonsniff-data` volume is the durable `/data` boundary and survives container replacement.

## Image choice, upgrades, and rollback

The public image requires no Portainer registry credentials:

- `ghcr.io/danielbrownjr/dragonsniff:latest` follows the newest release whose immutable image passed runtime validation.
- `ghcr.io/danielbrownjr/dragonsniff:sha-<full-commit-sha>` pins one immutable build.

To upgrade, pull the newest image and redeploy the stack. The named volume preserves evidence. For a reproducible deployment or rollback, replace `latest` with the desired full SHA tag and redeploy; keep the same volume declaration.

The currently published image is Linux/amd64. Confirm NAS architecture compatibility before deployment.

## Validated NAS behavior

The direct one-service topology has been manually verified on a reference NAS deployment: the configured authority was accepted, idle state showed **No current evidence**, persistent History survived replacement, and `/healthz` recovered after an intentional force-kill and restart. An already-open browser tab may briefly report `Local service error: Failed to fetch` while the process is unavailable; refresh or reconnect after `/healthz` returns.

Brave still warns that a JSONL download over plain LAN HTTP may be harmful. The response MIME type, download disposition, cache policy, and `nosniff` header have been reviewed and remain defensible, so this documentation pass does not alter them.

An ordinary TLS reverse proxy may still be useful when HTTPS is required. That is conceptually different from the retired proxy whose only purpose was to rewrite LAN `Host` or `Origin` values to localhost. HTTPS deployment and the remaining browser-download warning are tracked in [Issue #26](https://github.com/danielbrownjr/DragonSniff/issues/26).
