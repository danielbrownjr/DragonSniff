"""Derive and validate DragonSniff's narrowly supported release forms."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import re


RELEASE_TAG_PATTERN = re.compile(
    r"\Av([0-9]+)\.([0-9]+)\.([0-9]+)(?:-rc\.([0-9]+))?\Z"
)
PYTHON_VERSION_PATTERN = re.compile(
    r"\A([0-9]+)\.([0-9]+)\.([0-9]+)(?:rc([0-9]+))?\Z"
)
COMMIT_SHA_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z")
PROMOTION_TARGETS = frozenset({"version", "latest"})


@dataclass(frozen=True, slots=True)
class ReleaseVersion:
    python_version: str
    release_tag: str
    prerelease: bool


def derive_release_version(python_version: str) -> ReleaseVersion:
    """Derive the immutable public release spelling from a Python version."""
    match = PYTHON_VERSION_PATTERN.fullmatch(python_version)
    if match is None:
        raise ValueError(
            "Python version must be MAJOR.MINOR.PATCH or "
            "MAJOR.MINOR.PATCHrcN"
        )
    major, minor, patch, release_candidate = match.groups()
    release_tag = f"v{major}.{minor}.{patch}"
    if release_candidate is not None:
        release_tag += f"-rc.{release_candidate}"
    return ReleaseVersion(
        python_version=python_version,
        release_tag=release_tag,
        prerelease=release_candidate is not None,
    )


def release_version_from_tag(release_tag: str) -> ReleaseVersion:
    """Parse a supported Git/GHCR tag into its Python representation."""
    match = RELEASE_TAG_PATTERN.fullmatch(release_tag)
    if match is None:
        raise ValueError(
            "release tag must be vMAJOR.MINOR.PATCH or "
            "vMAJOR.MINOR.PATCH-rc.N"
        )
    major, minor, patch, release_candidate = match.groups()
    python_version = f"{major}.{minor}.{patch}"
    if release_candidate is not None:
        python_version += f"rc{release_candidate}"
    return ReleaseVersion(
        python_version=python_version,
        release_tag=release_tag,
        prerelease=release_candidate is not None,
    )


def validate_release_promotion(
    release_tag: str, package_version: str, promotion_target: str
) -> str:
    """Return the normalized Python version or raise for an unsafe request."""
    release = release_version_from_tag(release_tag)
    if promotion_target not in PROMOTION_TARGETS:
        raise ValueError("promotion target must be version or latest")
    if release.prerelease and promotion_target == "latest":
        raise ValueError("RC releases cannot promote latest")
    if release.python_version != package_version:
        raise ValueError(
            f"release tag normalizes to {release.python_version}, not "
            f"package version {package_version}"
        )
    return release.python_version


def validate_tag_target(release_sha: str, existing_tag_sha: str = "") -> None:
    """Reject malformed SHAs and a tag already bound to another commit."""
    if COMMIT_SHA_PATTERN.fullmatch(release_sha) is None:
        raise ValueError(
            "release SHA must be 40 lowercase hexadecimal characters"
        )
    if existing_tag_sha:
        if COMMIT_SHA_PATTERN.fullmatch(existing_tag_sha) is None:
            raise ValueError(
                "existing tag SHA must be 40 lowercase hexadecimal characters"
            )
        if existing_tag_sha != release_sha:
            raise ValueError(
                f"release tag already points to {existing_tag_sha}, not "
                f"{release_sha}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    derive = subparsers.add_parser("derive")
    derive.add_argument("python_version")

    promotion = subparsers.add_parser("validate-promotion")
    promotion.add_argument("release_tag")
    promotion.add_argument("package_version")
    promotion.add_argument("promotion_target")

    tag_target = subparsers.add_parser("validate-tag-target")
    tag_target.add_argument("release_sha")
    tag_target.add_argument("existing_tag_sha", nargs="?", default="")

    args = parser.parse_args()
    try:
        if args.command == "derive":
            release = derive_release_version(args.python_version)
            print(
                json.dumps(
                    {
                        "python_version": release.python_version,
                        "release_tag": release.release_tag,
                        "prerelease": release.prerelease,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        elif args.command == "validate-promotion":
            normalized = validate_release_promotion(
                args.release_tag, args.package_version, args.promotion_target
            )
            print(normalized)
        else:
            validate_tag_target(args.release_sha, args.existing_tag_sha)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
