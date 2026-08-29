"""Command-line entry point for the DragonSniff local service."""

from __future__ import annotations

import argparse

from .server import DragonSniffServer
from .target import TargetValidationError


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Observe one Dragon device locally")
    value.add_argument("--target", help="Dragon hostname, IP, or HTTP(S) origin")
    value.add_argument("--port", type=int, default=8765, help="loopback UI port (default: 8765)")
    return value


def main() -> int:
    args = parser().parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("port must be between 1 and 65535")
    server = DragonSniffServer(("127.0.0.1", args.port))
    if args.target:
        try:
            server.session_manager.start(args.target)
        except TargetValidationError as exc:
            server.server_close()
            raise SystemExit(str(exc)) from exc
    print(f"DragonSniff is listening at http://127.0.0.1:{args.port}")
    print("Press Ctrl+C to stop. No device mutation routes are available.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping DragonSniff.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

