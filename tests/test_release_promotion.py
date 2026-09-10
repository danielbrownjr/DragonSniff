from pathlib import Path
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

    def test_workflow_keeps_version_and_latest_promotions_separate(self) -> None:
        workflow = Path(".github/workflows/publish-container.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "python scripts/validate_release_promotion.py", workflow
        )
        self.assertIn("if: inputs.promotion_target == 'version'", workflow)
        self.assertIn("if: inputs.promotion_target == 'latest'", workflow)
