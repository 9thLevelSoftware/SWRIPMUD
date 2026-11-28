"""Unit checks for discovery, runtime isolation, and evidence requirements."""

import socket
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from .framework import TestCaseSpec, TestResult, get_registered_tests
from .manifest import EXCLUDED_CASES, EXPECTED_CASES, gate_passed, validate_registry
from .telnet_client import TelnetClient
from .tiers import load_all_tiers
from .wsl_env import parse_sanitizer_logs, require_private_network, stage_workspace


class GateTests(unittest.TestCase):
    def setUp(self):
        self.case = TestCaseSpec("safe", 1, "F01", "Normal boot", lambda _: None)
        self.result = TestResult("safe", 1, "F01", "Normal boot", "PASS", 0.1)
        self.summary = {
            "sanitizer_clean": True,
            "fixture_shutdowns": [{"clean_exit": True}],
            "aggregated_sanitizers": {"process_count": 1},
        }

    def test_registry_matches_the_adopted_case_inventory(self):
        load_all_tiers()
        specs = get_registered_tests()
        validate_registry(specs)
        self.assertFalse(EXPECTED_CASES.keys() & EXCLUDED_CASES.keys())
        with self.assertRaisesRegex(ValueError, "Missing"):
            validate_registry(specs[1:])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_registry([*specs, specs[0]])

    def test_gate_requires_exact_pass_results(self):
        self.assertTrue(gate_passed([self.case], [self.result], self.summary))
        for results in (
            [],
            [replace(self.result, id="different")],
            [replace(self.result, status="SKIP")],
            [replace(self.result, status="FAIL")],
            [replace(self.result, status="ERROR")],
            [self.result, self.result],
        ):
            with self.subTest(results=results):
                self.assertFalse(gate_passed([self.case], results, self.summary))

    def test_gate_requires_complete_fixture_and_sanitizer_evidence(self):
        for change in (
            {"sanitizer_clean": False},
            {"fixture_shutdowns": [{"clean_exit": False}]},
            {"fixture_shutdowns": [], "aggregated_sanitizers": {"process_count": 0}},
            {"aggregated_sanitizers": {"process_count": 0}},
        ):
            with self.subTest(change=change):
                self.assertFalse(gate_passed([self.case], [self.result], self.summary | change))

    def test_runtime_rejects_root_and_external_interfaces(self):
        with patch("os.geteuid", return_value=1000), patch("socket.if_nameindex", return_value=[(1, "lo")]):
            require_private_network()
        with patch("os.geteuid", return_value=0), patch("socket.if_nameindex", return_value=[(1, "lo")]):
            with self.assertRaisesRegex(RuntimeError, "non-root"):
                require_private_network()
        with patch("os.geteuid", return_value=1000), patch("socket.if_nameindex", return_value=[(1, "lo"), (2, "eth0")]):
            with self.assertRaisesRegex(RuntimeError, "only loopback"):
                require_private_network()

    def test_staging_rejects_symlinks_and_omits_source_accounts(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root)
            (source / "player").mkdir()
            (source / "player" / "Sample").write_text("source account", encoding="utf-8")
            (source / "gods").mkdir()
            (source / "gods" / "Sample").write_text("source record", encoding="utf-8")
            (source / "area").mkdir()
            (source / "area" / "world.are").write_text("fixture", encoding="utf-8")
            with patch("tests.e2e.wsl_env.tempfile.mkdtemp", side_effect=lambda **_: str(source / "staged")):
                (source / "staged").mkdir()
                staged = stage_workspace("unit", source)
                self.assertEqual(list((staged / "player").iterdir()), [])
                self.assertEqual(list((staged / "gods").iterdir()), [])
            (source / "area" / "link").symlink_to(source / "player" / "Sample")
            with self.assertRaisesRegex(ValueError, "Symlink in staged fixture"):
                stage_workspace("unit-link", source)

    def test_sanitizer_reports_stay_bound_to_the_process(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / "asan-a.log.123").write_text("AddressSanitizer:DEADLYSIGNAL", encoding="utf-8")
            self.assertFalse(parse_sanitizer_logs(path, "boot", "asan-a.log").clean)
            self.assertTrue(parse_sanitizer_logs(path, "boot", "asan-b.log").clean)


class LoginTests(unittest.TestCase):
    def test_reconnect_waits_for_game_prompt_without_title_input(self):
        def serve(peer):
            with peer.makefile("rb") as commands:
                peer.sendall(b"Name: ")
                self.assertEqual(commands.readline().strip(), b"Immtest")
                peer.sendall(b"Password: ")
                self.assertEqual(commands.readline().strip(), b"LocalFixture")
                peer.sendall(b"Reconnecting.\r\n")
                peer.sendall(b"\x1b[36mHealth: 40/40 Movement: 10/10 >\x1b[0m")
                self.assertEqual(commands.readline().strip(), b"shutdown mud now")
                peer.sendall(b"Shutdown by Immtest.\r\n")

        client_socket, peer = socket.socketpair()
        with client_socket, peer, ThreadPoolExecutor(max_workers=1) as executor:
            peer.settimeout(2.0)
            server = executor.submit(serve, peer)

            def connect_to_peer(client):
                client.sock = client_socket

            with patch.object(TelnetClient, "connect", connect_to_peer):
                with TelnetClient() as client:
                    transcript = client.login("Immtest", "LocalFixture", timeout=1.0)
                    self.assertIn("Reconnecting.", transcript)
                    self.assertIn("Health: 40/40", transcript)
                    client.send_line("shutdown mud now")
                    self.assertIn("Shutdown by Immtest.", client.read_until("Shutdown by Immtest.", timeout=1.0))
            server.result(timeout=2.0)

    def test_shutdown_login_uses_authenticated_transition_with_custom_prompt(self):
        def serve(peer, reconnect):
            with peer.makefile("rb") as commands:
                peer.sendall(b"Name: ")
                self.assertEqual(commands.readline().strip(), b"Immtest")
                peer.sendall(b"Password: ")
                self.assertEqual(commands.readline().strip(), b"LocalFixture")
                if reconnect:
                    peer.sendall(b"Reconnecting.\r\n")
                else:
                    peer.sendall(b"Press enter...\r\n")
                    self.assertEqual(commands.readline(), b"\r\n")
                    peer.sendall(b"Press [ENTER] ")
                    self.assertEqual(commands.readline(), b"\r\n")
                    peer.sendall(b"Welcome to Rise in Power...\r\n")
                peer.sendall(b"1000/1000 0/0>")
                self.assertEqual(commands.readline().strip(), b"shutdown mud now")
                peer.sendall(b"Shutdown by Immtest.\r\n")

        for reconnect, marker in (
            (True, "Reconnecting."),
            (False, "Welcome to Rise in Power..."),
        ):
            with self.subTest(reconnect=reconnect):
                client_socket, peer = socket.socketpair()
                with client_socket, peer, ThreadPoolExecutor(max_workers=1) as executor:
                    peer.settimeout(2.0)
                    server = executor.submit(serve, peer, reconnect)

                    def connect_to_peer(client):
                        client.sock = client_socket

                    with patch.object(TelnetClient, "connect", connect_to_peer):
                        with TelnetClient() as client:
                            transcript = client.login("Immtest", "LocalFixture", timeout=1.0, wait_for_prompt=False)
                            self.assertIn(marker, transcript)
                            client.send_line("shutdown mud now")
                            self.assertIn("Shutdown by Immtest.", client.read_until("Shutdown by Immtest.", timeout=1.0))
                    server.result(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
