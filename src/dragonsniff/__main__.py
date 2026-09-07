"""Command-line entry point for the DragonSniff local service."""

from __future__ import annotations

import argparse
import logging
import os
import signal
from threading import Event, Thread
from types import FrameType

from .server import DragonSniffServer, SessionManager
from .storage import (
    DEFAULT_RETENTION_BYTES,
    DEFAULT_RETENTION_SESSIONS,
    SessionStore,
)
from .target import TargetValidationError


DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8765
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if result < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return result


def _environment_targets() -> list[str]:
    return [
        value.strip()
        for value in os.environ.get("DRAGONSNIFF_ALLOWED_TARGETS", "").split(",")
        if value.strip()
    ]


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Observe one Dragon device locally")
    value.add_argument("--target", help="Dragon hostname, IP, or HTTP(S) origin")
    value.add_argument(
        "--bind",
        choices=("127.0.0.1", "0.0.0.0"),
        default=os.environ.get("DRAGONSNIFF_BIND", DEFAULT_BIND),
        help=(
            "listen address (default: 127.0.0.1); 0.0.0.0 is intended only "
            "for a container published to host loopback"
        ),
    )
    value.add_argument(
        "--port",
        type=_port,
        default=os.environ.get("DRAGONSNIFF_PORT", str(DEFAULT_PORT)),
        help="UI port (default: 8765)",
    )
    value.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default=os.environ.get("DRAGONSNIFF_LOG_LEVEL", "INFO").upper(),
        help="service log level (default: INFO)",
    )
    value.add_argument(
        "--data-dir",
        default=os.environ.get("DRAGONSNIFF_DATA_DIR"),
        help="persist session evidence under this directory",
    )
    value.add_argument(
        "--retention-bytes",
        type=_positive_int,
        default=os.environ.get(
            "DRAGONSNIFF_RETENTION_BYTES", str(DEFAULT_RETENTION_BYTES)
        ),
        help="maximum retained session storage (default: 256 MiB)",
    )
    value.add_argument(
        "--retention-sessions",
        type=_positive_int,
        default=os.environ.get(
            "DRAGONSNIFF_RETENTION_SESSIONS", str(DEFAULT_RETENTION_SESSIONS)
        ),
        help="maximum retained session count (default: 500)",
    )
    value.add_argument(
        "--allow-target",
        action="append",
        default=_environment_targets(),
        help="permitted Dragon target; may be repeated",
    )
    value.add_argument(
        "--require-allowlist",
        action="store_true",
        default=os.environ.get("DRAGONSNIFF_REQUIRE_ALLOWLIST", "").lower()
        in {"1", "true", "yes"},
        help="refuse startup unless at least one target is explicitly allowed",
    )
    return value


def _serve(server: DragonSniffServer) -> None:
    shutdown_started = Event()

    def request_shutdown(_signum: int, _frame: FrameType | None) -> None:
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        Thread(
            target=server.shutdown,
            name="dragonsniff-shutdown",
            daemon=True,
        ).start()

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, request_shutdown)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping DragonSniff.")
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        server.server_close()


def main() -> int:
    args = parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.require_allowlist and not args.allow_target:
        raise SystemExit("at least one --allow-target is required")
    store = (
        SessionStore(
            args.data_dir,
            retention_bytes=args.retention_bytes,
            retention_sessions=args.retention_sessions,
        )
        if args.data_dir
        else None
    )
    manager = SessionManager(store=store, allowed_targets=args.allow_target)
    server = DragonSniffServer(
        (args.bind, args.port),
        manager,
        allow_wildcard_bind=args.bind == "0.0.0.0",
    )
    if args.target:
        try:
            server.session_manager.start(args.target)
        except TargetValidationError as exc:
            server.server_close()
            raise SystemExit(str(exc)) from exc
    display_host = "127.0.0.1" if args.bind == "0.0.0.0" else args.bind
    print(f"DragonSniff is listening at http://{display_host}:{server.server_port}")
    print("Press Ctrl+C to stop. No device mutation routes are available.")
    _serve(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
