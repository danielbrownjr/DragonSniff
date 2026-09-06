import os
import signal
from threading import Event
from unittest import TestCase
from unittest.mock import patch

from dragonsniff.__main__ import DEFAULT_BIND, DEFAULT_PORT, _serve, parser


class CommandLineTests(TestCase):
    def test_defaults_remain_local(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            args = parser().parse_args([])

        self.assertEqual(args.bind, DEFAULT_BIND)
        self.assertEqual(args.port, DEFAULT_PORT)
        self.assertEqual(args.log_level, "INFO")

    def test_server_settings_can_come_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DRAGONSNIFF_BIND": "0.0.0.0",
                "DRAGONSNIFF_PORT": "9876",
                "DRAGONSNIFF_LOG_LEVEL": "warning",
            },
            clear=True,
        ):
            args = parser().parse_args([])

        self.assertEqual(args.bind, "0.0.0.0")
        self.assertEqual(args.port, 9876)
        self.assertEqual(args.log_level, "WARNING")

    def test_command_line_overrides_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"DRAGONSNIFF_PORT": "9876", "DRAGONSNIFF_LOG_LEVEL": "ERROR"},
            clear=True,
        ):
            args = parser().parse_args(["--port", "8766", "--log-level", "DEBUG"])

        self.assertEqual(args.port, 8766)
        self.assertEqual(args.log_level, "DEBUG")

    def test_invalid_port_is_rejected(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                parser().parse_args(["--port", "0"])
            with self.assertRaises(SystemExit):
                parser().parse_args(["--port", "not-a-port"])


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
