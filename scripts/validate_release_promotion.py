"""Validate DragonSniff's narrowly supported release promotion forms."""

from __future__ import annotations

import argparse
import re


RELEASE_TAG_PATTERN = re.compile(
    r"\Av([0-9]+)\.([0-9]+)\.([0-9]+)(?:-rc\.([0-9]+))?\Z"
)
PROMOTION_TARGETS = frozenset({"version", "latest"})


def validate_release_promotion(
    release_tag: str, package_version: str, promotion_target: str
) -> str:
    """Return the normalized Python version or raise for an unsafe request."""
    match = RELEASE_TAG_PATTERN.fullmatch(release_tag)
    if match is None:
        raise ValueError(
            "release tag must be vMAJOR.MINOR.PATCH or "
            "vMAJOR.MINOR.PATCH-rc.N"
        )
    if promotion_target not in PROMOTION_TARGETS:
        raise ValueError("promotion target must be version or latest")

    major, minor, patch, release_candidate = match.groups()
    normalized = f"{major}.{minor}.{patch}"
    if release_candidate is not None:
        normalized += f"rc{release_candidate}"
        if promotion_target == "latest":
            raise ValueError("RC releases cannot promote latest")

    if normalized != package_version:
        raise ValueError(
            f"release tag normalizes to {normalized}, not package version "
            f"{package_version}"
        )
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_tag")
    parser.add_argument("package_version")
    parser.add_argument("promotion_target")
    args = parser.parse_args()
    try:
        normalized = validate_release_promotion(
            args.release_tag, args.package_version, args.promotion_target
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(normalized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
