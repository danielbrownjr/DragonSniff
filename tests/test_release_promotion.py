from pathlib import Path
import json
import subprocess
import sys
from unittest import TestCase


SCRIPT = Path("scripts/validate_release_promotion.py")


class ReleasePromotionTests(TestCase):
    def run_validator(
        self, release_tag: str, package_version: str, promotion_target: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "validate-promotion",
                release_tag,
                package_version,
                promotion_target,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def assert_valid(
        self,
        release_tag: str,
        package_version: str,
        promotion_target: str = "version",
    ) -> None:
        result = self.run_validator(
            release_tag, package_version, promotion_target
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), package_version)
        self.assertEqual(result.stderr, "")

    def assert_invalid(
        self,
        release_tag: str,
        package_version: str = "0.5.0",
        promotion_target: str = "version",
    ) -> str:
        result = self.run_validator(
            release_tag, package_version, promotion_target
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        return result.stderr

    def test_supported_tags_normalize_to_python_versions(self) -> None:
        for release_tag, package_version in (
            ("v0.5.0", "0.5.0"),
            ("v0.5.0-rc.1", "0.5.0rc1"),
            ("v1.2.3-rc.12", "1.2.3rc12"),
            ("v12.34.56-rc.7", "12.34.56rc7"),
        ):
            with self.subTest(release_tag=release_tag):
                self.assert_valid(release_tag, package_version)

    def test_supported_python_versions_derive_release_identity(self) -> None:
        for python_version, release_tag, prerelease in (
            ("0.5.0", "v0.5.0", False),
            ("0.5.0rc1", "v0.5.0-rc.1", True),
            ("0.5.0rc12", "v0.5.0-rc.12", True),
        ):
            with self.subTest(python_version=python_version):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "derive",
                        python_version,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    json.loads(result.stdout),
                    {
                        "prerelease": prerelease,
                        "python_version": python_version,
                        "release_tag": release_tag,
                    },
                )

    def test_unsupported_python_release_versions_are_rejected(self) -> None:
        for python_version in (
            "0.5",
            "0.5.0a1",
            "0.5.0b1",
            "0.5.0.dev1",
            "0.5.0.post1",
            "0.5.0+foo",
        ):
            with self.subTest(python_version=python_version):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "derive",
                        python_version,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Python version must be", result.stderr)

    def test_unsupported_tag_forms_are_rejected(self) -> None:
        for release_tag in (
            "0.5.0",
            "v0.5",
            "v0.5.0-rc",
            "v0.5.0-rc.foo",
            "v0.5.0-beta.1",
            "v0.5.0+meta",
        ):
            with self.subTest(release_tag=release_tag):
                error = self.assert_invalid(release_tag)
                self.assertIn("release tag must be", error)

    def test_package_version_mismatch_is_rejected(self) -> None:
        error = self.assert_invalid("v0.5.0-rc.1", "0.5.0rc2")
        self.assertIn("not package version", error)

    def test_rc_can_promote_version_but_not_latest(self) -> None:
        self.assert_valid("v0.5.0-rc.1", "0.5.0rc1", "version")
        error = self.assert_invalid(
            "v0.5.0-rc.1", "0.5.0rc1", "latest"
        )
        self.assertIn("RC releases cannot promote latest", error)

    def test_stable_release_keeps_both_existing_promotion_paths(self) -> None:
        self.assert_valid("v0.5.0", "0.5.0", "version")
        self.assert_valid("v0.5.0", "0.5.0", "latest")

    def test_unknown_promotion_target_is_rejected(self) -> None:
        error = self.assert_invalid("v0.5.0", "0.5.0", "other")
        self.assertIn("promotion target must be", error)

    def test_existing_tag_target_is_idempotent_but_not_rebindable(self) -> None:
        release_sha = "1" * 40
        same = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "validate-tag-target",
                release_sha,
                release_sha,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(same.returncode, 0, same.stderr)

        collision = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "validate-tag-target",
                release_sha,
                "2" * 40,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(collision.returncode, 0)
        self.assertIn("already points to", collision.stderr)

    def test_missing_tag_can_be_created_only_for_valid_release_sha(
        self,
    ) -> None:
        valid = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "validate-tag-target",
                "1" * 40,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)
        invalid = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "validate-tag-target",
                "not-a-sha",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("release SHA must be", invalid.stderr)

    def test_workflow_keeps_version_and_latest_promotions_separate(self) -> None:
        workflow = Path(".github/workflows/publish-container.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "uses: ./.github/workflows/promote-container.yml", workflow
        )
        promotion = Path(
            ".github/workflows/promote-container.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("validate-promotion", promotion)
        self.assertIn("RC releases cannot promote latest", SCRIPT.read_text())
        self.assertIn("Refusing to overwrite", promotion)
        self.assertIn("refusing to write", promotion)
        self.assertIn('test "$status" = 404', promotion)

    def test_release_workflow_orders_side_effects_and_protects_latest(self) -> None:
        workflow = Path(".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("version:\n", workflow)
        self.assertIn("needs: [preflight, tag]", workflow)
        self.assertIn("needs: [preflight, promote-version]", workflow)
        self.assertIn("needs: [preflight, github-release]", workflow)
        self.assertIn(
            "if: needs.preflight.outputs.prerelease == 'false'", workflow
        )
        self.assertIn("promotion_target: latest", workflow)
        self.assertIn("--verify-tag", workflow)
        self.assertIn("does not match package version", workflow)
        self.assertNotIn("git tag --force", workflow)
