from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class ContainerDefinitionTests(TestCase):
    def test_image_runs_as_non_root_with_healthcheck_and_persistent_data(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("USER dragonsniff:dragonsniff", dockerfile)
        self.assertIn('VOLUME ["/data"]', dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/healthz", dockerfile)
        self.assertIn("DRAGONSNIFF_REQUIRE_ALLOWLIST=1", dockerfile)

    def test_compose_publishes_only_to_loopback_and_mounts_data_volume(self) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:8765:8765"', compose)
        self.assertNotIn('- "8765:8765"', compose)
        self.assertIn("dragonsniff-data:/data", compose)
        self.assertIn("read_only: true", compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("DRAGONSNIFF_ALLOWED_TARGETS", compose)
