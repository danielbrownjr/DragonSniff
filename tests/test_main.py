import os
import signal
from threading import Event
from unittest import TestCase
from unittest.mock import patch

from dragonsniff.__main__ import (
    DEFAULT_BIND,
    DEFAULT_PORT,
    _listening_message,
    _serve,
    main,
    parser,
)


class CommandLineTests(TestCase):
    def test_defaults_remain_local(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            args = parser().parse_args([])

        self.assertEqual(args.bind, DEFAULT_BIND)
        self.assertEqual(args.port, DEFAULT_PORT)
        self.assertEqual(args.log_level, "INFO")
        self.assertEqual(args.allow_host, [])

    def test_server_settings_can_come_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DRAGONSNIFF_BIND": "0.0.0.0",
                "DRAGONSNIFF_PORT": "9876",
                "DRAGONSNIFF_LOG_LEVEL": "warning",
                "DRAGONSNIFF_DATA_DIR": "C:/dragon-data",
                "DRAGONSNIFF_RETENTION_BYTES": "12345",
                "DRAGONSNIFF_RETENTION_SESSIONS": "42",
                "DRAGONSNIFF_ALLOWED_TARGETS": "dragon.local, http://192.0.2.4",
                "DRAGONSNIFF_ALLOWED_HOSTS": (
                    "192.0.2.10:8766, dragonsniff.home.arpa:443"
                ),
                "DRAGONSNIFF_REQUIRE_ALLOWLIST": "true",
            },
            clear=True,
        ):
            args = parser().parse_args([])

        self.assertEqual(args.bind, "0.0.0.0")
        self.assertEqual(args.port, 9876)
        self.assertEqual(args.log_level, "WARNING")
        self.assertEqual(args.data_dir, "C:/dragon-data")
        self.assertEqual(args.retention_bytes, 12345)
        self.assertEqual(args.retention_sessions, 42)
        self.assertEqual(args.allow_target, ["dragon.local", "http://192.0.2.4"])
        self.assertEqual(
            args.allow_host,
            ["192.0.2.10:8766", "dragonsniff.home.arpa:443"],
        )
        self.assertTrue(args.require_allowlist)

    def test_command_line_overrides_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"DRAGONSNIFF_PORT": "9876", "DRAGONSNIFF_LOG_LEVEL": "ERROR"},
            clear=True,
        ):
            args = parser().parse_args(["--port", "8766", "--log-level", "DEBUG"])

        self.assertEqual(args.port, 8766)
        self.assertEqual(args.log_level, "DEBUG")

    def test_command_line_allowed_hosts_extend_environment_hosts(self) -> None:
        with patch.dict(
            os.environ,
            {"DRAGONSNIFF_ALLOWED_HOSTS": "nas.example:8766"},
            clear=True,
        ):
            args = parser().parse_args(
                ["--allow-host", "192.0.2.10:8766"]
            )

        self.assertEqual(
            args.allow_host,
            ["nas.example:8766", "192.0.2.10:8766"],
        )

    def test_invalid_port_is_rejected(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                parser().parse_args(["--port", "0"])
            with self.assertRaises(SystemExit):
                parser().parse_args(["--port", "not-a-port"])

    def test_malformed_allowed_host_is_rejected_from_cli_or_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                parser().parse_args(["--allow-host", "*"])
        with (
            patch.dict(
                os.environ,
                {"DRAGONSNIFF_ALLOWED_HOSTS": "http://192.0.2.10:8766"},
                clear=True,
            ),
            self.assertRaisesRegex(SystemExit, "DRAGONSNIFF_ALLOWED_HOSTS"),
        ):
            parser()

    def test_persistence_options_can_be_supplied_explicitly(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            args = parser().parse_args(
                [
                    "--data-dir",
                    "data",
                    "--retention-bytes",
                    "4096",
                    "--retention-sessions",
                    "12",
                    "--allow-target",
                    "dragon.local",
                    "--allow-target",
                    "192.0.2.4",
                    "--allow-host",
                    "192.0.2.10:8766",
                    "--allow-host",
                    "dragonsniff.home.arpa:443",
                    "--require-allowlist",
                ]
            )
        self.assertEqual(args.data_dir, "data")
        self.assertEqual(args.retention_bytes, 4096)
        self.assertEqual(args.retention_sessions, 12)
        self.assertEqual(args.allow_target, ["dragon.local", "192.0.2.4"])
        self.assertEqual(
            args.allow_host,
            ["192.0.2.10:8766", "dragonsniff.home.arpa:443"],
        )
        self.assertTrue(args.require_allowlist)

    def test_listening_message_does_not_invent_a_container_url(self) -> None:
        self.assertEqual(
            _listening_message("0.0.0.0", 8765),
            "DragonSniff is listening on all interfaces at port 8765.",
        )
        self.assertEqual(
            _listening_message("127.0.0.1", 8765),
            "DragonSniff is listening at http://127.0.0.1:8765",
        )

    def test_required_allowlist_refuses_service_start_without_targets(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("sys.argv", ["dragonsniff", "--require-allowlist"]),
            self.assertRaisesRegex(SystemExit, "at least one --allow-target"),
        ):
            main()


class ServiceLifecycleTests(TestCase):
    def test_sigterm_requests_shutdown_and_closes_server(self) -> None:
        class FakeServer:
            def __init__(self) -> None:
                self.shutdown_called = Event()
                self.closed = False

            def shutdown(self) -> None:
                self.shutdown_called.set()

            def serve_forever(self) -> None:
                registered["handler"](signal.SIGTERM, None)
                self.shutdown_called.wait(1)

            def server_close(self) -> None:
                self.closed = True

        registered = {}

        def install(signum, handler):
            if callable(handler):
                registered["handler"] = handler

        server = FakeServer()
        with (
            patch("dragonsniff.__main__.signal.getsignal", return_value="previous"),
            patch("dragonsniff.__main__.signal.signal", side_effect=install) as set_signal,
        ):
            _serve(server)  # type: ignore[arg-type]

        self.assertTrue(server.shutdown_called.is_set())
        self.assertTrue(server.closed)
        set_signal.assert_any_call(signal.SIGTERM, "previous")
