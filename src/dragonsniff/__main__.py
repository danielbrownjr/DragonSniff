"""Command-line entry point for the DragonSniff local service."""

from __future__ import annotations

import argparse
import logging
import os
import signal
from threading import Event, Thread
from types import FrameType

from .server import DragonSniffServer
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
    server = DragonSniffServer(
        (args.bind, args.port),
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
