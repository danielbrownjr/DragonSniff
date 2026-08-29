"""Validation and normalization for explicitly supplied Dragon addresses."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


class TargetValidationError(ValueError):
    """Raised when a device target is not an address DragonSniff can observe."""


@dataclass(frozen=True, slots=True)
class DeviceTarget:
    base_url: str
    display_address: str

    def endpoint(self, path: str) -> str:
        if not path.startswith("/api/v2/"):
            raise ValueError("device endpoints must be fixed API v2 paths")
        return f"{self.base_url}{path}"


def parse_target(value: str) -> DeviceTarget:
    """Accept a hostname, IP, or explicit HTTP(S) origin and reject URL extras."""

    supplied = value.strip()
    if not supplied:
        raise TargetValidationError("enter a Dragon hostname or address")
    candidate = supplied if "://" in supplied else f"http://{supplied}"
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise TargetValidationError(f"invalid target: {exc}") from exc

    if parsed.scheme not in {"http", "https"}:
        raise TargetValidationError("target scheme must be http or https")
    if not parsed.hostname:
        raise TargetValidationError("target must include a hostname or IP address")
    if parsed.username is not None or parsed.password is not None:
        raise TargetValidationError("credentials are not accepted in the target URL")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise TargetValidationError("target must be an origin without a path, query, or fragment")

    host = parsed.hostname
    rendered_host = f"[{host}]" if ":" in host else host
    authority = rendered_host if port is None else f"{rendered_host}:{port}"
    return DeviceTarget(
        base_url=f"{parsed.scheme.lower()}://{authority}",
        display_address=authority,
    )
