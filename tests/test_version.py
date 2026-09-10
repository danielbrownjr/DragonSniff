from pathlib import Path
import tomllib
from unittest import TestCase

import dragonsniff
from dragonsniff.client import DragonClient
from dragonsniff.server import DragonSniffHandler


class VersionContractTests(TestCase):
    def test_source_tree_exports_release_version(self) -> None:
        self.assertEqual(dragonsniff.__version__, "0.5.0rc1")

    def test_package_metadata_uses_the_authoritative_version_module(self) -> None:
        project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        self.assertNotIn("version", project["project"])
        self.assertIn("version", project["project"]["dynamic"])
        self.assertEqual(
            project["tool"]["setuptools"]["dynamic"]["version"],
            {"attr": "dragonsniff._version.__version__"},
        )

    def test_http_identifiers_use_the_release_version(self) -> None:
        request = DragonClient._request("http://dragon.example/api/v2/info")

        self.assertEqual(
            request.get_header("User-agent"), "DragonSniff/0.5.0rc1"
        )
        self.assertEqual(
            DragonSniffHandler.server_version, "DragonSniff/0.5.0rc1"
        )
