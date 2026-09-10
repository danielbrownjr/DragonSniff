"""Verify the identity and minimum contents of a DragonSniff release wheel."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile


WHEEL_NAME_PATTERN = re.compile(
    r"\Adragonsniff-([A-Za-z0-9_.!+]+)-py3-none-any\.whl\Z"
)
REQUIRED_PATHS = frozenset(
    {
        "dragonsniff/__init__.py",
        "dragonsniff/__main__.py",
        "dragonsniff/_version.py",
        "dragonsniff/web/app.js",
        "dragonsniff/web/index.html",
        "dragonsniff/web/payload.js",
        "dragonsniff/web/style.css",
    }
)


def verify_release_wheel(path: Path, expected_version: str) -> None:
    """Raise ValueError unless *path* is the expected release wheel."""
    match = WHEEL_NAME_PATTERN.fullmatch(path.name)
    if match is None or match.group(1) != expected_version:
        raise ValueError(
            f"wheel filename must be dragonsniff-{expected_version}-py3-none-any.whl"
        )
    try:
        with ZipFile(path) as wheel:
            names = frozenset(wheel.namelist())
            missing = sorted(REQUIRED_PATHS - names)
            if missing:
                raise ValueError(
                    "wheel is missing required content: " + ", ".join(missing)
                )
            metadata_names = [
                name
                for name in names
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                raise ValueError("wheel must contain exactly one METADATA file")
            metadata = BytesParser().parsebytes(wheel.read(metadata_names[0]))
    except (BadZipFile, OSError) as exc:
        raise ValueError(f"wheel cannot be read: {exc}") from exc
    if metadata.get("Name") != "dragonsniff":
        raise ValueError("wheel metadata Name is not dragonsniff")
    if metadata.get("Version") != expected_version:
        raise ValueError(
            f"wheel metadata version {metadata.get('Version')!r} does not "
            f"match {expected_version}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("expected_version")
    args = parser.parse_args()
    try:
        verify_release_wheel(args.wheel, args.expected_version)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
