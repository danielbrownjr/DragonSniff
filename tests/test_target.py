from unittest import TestCase

from dragonsniff.target import TargetValidationError, parse_target


class TargetTests(TestCase):
    def test_normalizes_hostname_and_explicit_origin(self) -> None:
        self.assertEqual(parse_target("dragonbreath.local").base_url, "http://dragonbreath.local")
        self.assertEqual(parse_target("https://10.0.0.8:8443/").base_url, "https://10.0.0.8:8443")
        self.assertEqual(parse_target("[fe80::1]:8080").base_url, "http://[fe80::1]:8080")

    def test_rejects_empty_credentials_and_url_extras(self) -> None:
        invalid = (
            "",
            "ftp://dragon.local",
            "http://user:pass@dragon.local",
            "http://dragon.local/api/v2/state",
            "http://dragon.local?query=yes",
            "http://dragon.local/#fragment",
            "http://:80",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(TargetValidationError):
                parse_target(value)

    def test_endpoint_rejects_generic_proxy_paths(self) -> None:
        target = parse_target("dragon.local")
        self.assertEqual(target.endpoint("/api/v2/info"), "http://dragon.local/api/v2/info")
        with self.assertRaises(ValueError):
            target.endpoint("/api/v1/settings")
