from pathlib import Path
import tempfile
from unittest import TestCase
from zipfile import ZipFile

from scripts.verify_release_wheel import REQUIRED_PATHS, verify_release_wheel


class ReleaseWheelTests(TestCase):
    def make_wheel(
        self,
        directory: str,
        *,
        filename_version: str = "0.5.0rc1",
        metadata_version: str = "0.5.0rc1",
        omit: str | None = None,
    ) -> Path:
        path = Path(directory) / (
            f"dragonsniff-{filename_version}-py3-none-any.whl"
        )
        with ZipFile(path, "w") as wheel:
            for name in REQUIRED_PATHS:
                if name != omit:
                    wheel.writestr(name, "")
            wheel.writestr(
                "dragonsniff-0.5.0rc1.dist-info/METADATA",
                "Metadata-Version: 2.1\n"
                "Name: dragonsniff\n"
                f"Version: {metadata_version}\n",
            )
        return path

    def test_expected_release_wheel_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel = self.make_wheel(temporary)
            verify_release_wheel(wheel, "0.5.0rc1")

    def test_filename_and_metadata_versions_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wrong_name = self.make_wheel(
                temporary, filename_version="0.5.0rc2"
            )
            with self.assertRaisesRegex(ValueError, "wheel filename"):
                verify_release_wheel(wrong_name, "0.5.0rc1")
        with tempfile.TemporaryDirectory() as temporary:
            wrong_metadata = self.make_wheel(
                temporary, metadata_version="0.5.0rc2"
            )
            with self.assertRaisesRegex(ValueError, "metadata version"):
                verify_release_wheel(wrong_metadata, "0.5.0rc1")

    def test_required_runtime_content_must_be_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel = self.make_wheel(
                temporary, omit="dragonsniff/web/app.js"
            )
            with self.assertRaisesRegex(ValueError, "missing required content"):
                verify_release_wheel(wheel, "0.5.0rc1")
