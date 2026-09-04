import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "herdr-pane-switch.py"
MODULE_SPEC = importlib.util.spec_from_file_location("herdr_pane_switch", SCRIPT_PATH)
herdr_pane_switch = importlib.util.module_from_spec(MODULE_SPEC)
assert MODULE_SPEC.loader is not None
MODULE_SPEC.loader.exec_module(herdr_pane_switch)


class PaneOrderingTests(unittest.TestCase):
    def test_documented_layouts_follow_visual_geometry_order(self):
        fixtures = {
            "11": (
                [("pB", 100, 0), ("p2", 0, 0)],
                ["p2", "pB"],
            ),
            "12": (
                [("pT", 100, 50), ("p2", 0, 0), ("pB", 100, 0)],
                ["p2", "pB", "pT"],
            ),
            "21": (
                [("pT", 100, 0), ("p2", 0, 50), ("pB", 0, 0)],
                ["pB", "pT", "p2"],
            ),
            "22": (
                [("pT", 100, 50), ("p2", 0, 50), ("pB", 100, 0), ("pX", 0, 0)],
                ["pX", "pB", "p2", "pT"],
            ),
            "13": (
                [("pT", 100, 75), ("p2", 0, 0), ("pB", 100, 0), ("pX", 100, 35)],
                ["p2", "pB", "pX", "pT"],
            ),
            "31": (
                [("pT", 100, 0), ("p2", 0, 75), ("pB", 0, 0), ("pX", 0, 35)],
                ["pB", "pT", "pX", "p2"],
            ),
            "111": (
                [("pT", 200, 0), ("p2", 0, 0), ("pB", 100, 0)],
                ["p2", "pB", "pT"],
            ),
        }

        for layout, (fixture, expected) in fixtures.items():
            panes = [
                {"pane_id": pane_id, "rect": {"x": x, "y": y}}
                for pane_id, x, y in fixture
            ]
            with self.subTest(layout=layout):
                ordered = herdr_pane_switch.sort_panes_by_geometry(panes)
                self.assertEqual([pane["pane_id"] for pane in ordered], expected)


class LayoutResponseTests(unittest.TestCase):
    def test_layout_response_exposes_valid_pane_geometry(self):
        response = {
            "id": "cli:pane:layout",
            "result": {
                "layout": {
                    "workspace_id": "w1",
                    "tab_id": "w1:t1",
                    "focused_pane_id": "w1:p2",
                    "panes": [
                        {"pane_id": "w1:p2", "rect": {"x": 0, "y": 0}},
                        {"pane_id": "w1:pB", "rect": {"x": 100, "y": 0}},
                    ],
                    "splits": [],
                    "area": {"x": 0, "y": 0, "width": 200, "height": 100},
                    "zoomed": False,
                },
                "type": "pane_layout",
            },
        }

        layout = herdr_pane_switch.parse_layout_response(json.dumps(response))

        self.assertEqual(layout["workspace_id"], "w1")
        self.assertEqual([pane["pane_id"] for pane in layout["panes"]], ["w1:p2", "w1:pB"])


class SocketResponseTests(unittest.TestCase):
    def test_socket_response_must_report_the_requested_pane(self):
        response = {
            "id": "1",
            "result": {
                "type": "pane_info",
                "pane": {"pane_id": "w-custom:p2"},
            },
        }

        with self.assertRaises(herdr_pane_switch.HerdrError):
            herdr_pane_switch.parse_socket_response(
                json.dumps(response), expected_pane_id="w-custom:pB"
            )

    def test_focus_uses_injected_socket_and_validates_selected_pane(self):
        socket_path = "/tmp/herdr-recipes-custom.sock"
        response = {
            "id": "1",
            "result": {
                "type": "pane_info",
                "pane": {"pane_id": "w-custom:pB"},
            },
        }
        fake_socket = mock.Mock()
        fake_socket.recv.return_value = (json.dumps(response) + "\n").encode()

        with mock.patch.object(herdr_pane_switch.socket, "socket", return_value=fake_socket):
            with mock.patch.object(
                herdr_pane_switch.select, "select", return_value=([fake_socket], [], [])
            ):
                with mock.patch.dict(os.environ, {"HERDR_SOCKET_PATH": socket_path}):
                    result = herdr_pane_switch.socket_rpc(
                        "pane.focus",
                        {"pane_id": "w-custom:pB"},
                        expected_pane_id="w-custom:pB",
                    )

        fake_socket.connect.assert_called_once_with(socket_path)
        request = json.loads(fake_socket.sendall.call_args.args[0].decode())
        self.assertEqual(request["method"], "pane.focus")
        self.assertEqual(request["params"], {"pane_id": "w-custom:pB"})
        self.assertEqual(result["pane"]["pane_id"], "w-custom:pB")

    def test_focus_accepts_a_response_split_across_socket_reads(self):
        response = {
            "id": "1",
            "result": {
                "type": "pane_info",
                "pane": {"pane_id": "w-custom:pB"},
            },
        }
        payload = (json.dumps(response) + "\n").encode()
        fake_socket = mock.Mock()
        fake_socket.recv.side_effect = [payload[:8], payload[8:]]

        with mock.patch.object(herdr_pane_switch.socket, "socket", return_value=fake_socket):
            with mock.patch.object(
                herdr_pane_switch.select, "select", return_value=([fake_socket], [], [])
            ):
                with mock.patch.dict(os.environ, {"HERDR_SOCKET_PATH": "/tmp/custom.sock"}):
                    result = herdr_pane_switch.socket_rpc(
                        "pane.focus",
                        {"pane_id": "w-custom:pB"},
                        expected_pane_id="w-custom:pB",
                    )

        self.assertEqual(result["pane"]["pane_id"], "w-custom:pB")
        self.assertEqual(fake_socket.recv.call_count, 2)

    def test_socket_path_falls_back_to_the_default_session_socket(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                herdr_pane_switch.resolve_socket_path(),
                herdr_pane_switch.DEFAULT_SOCKET_PATH,
            )


class CommandTests(unittest.TestCase):
    def test_cli_uses_current_layout_and_focuses_visual_index(self):
        layout_response = {
            "id": "cli:pane:layout",
            "result": {
                "layout": {
                    "workspace_id": "w1",
                    "tab_id": "w1:t1",
                    "focused_pane_id": "w1:p2",
                    "panes": [
                        {"pane_id": "w1:pT", "rect": {"x": 100, "y": 50}},
                        {"pane_id": "w1:p2", "rect": {"x": 0, "y": 0}},
                        {"pane_id": "w1:pB", "rect": {"x": 100, "y": 0}},
                        {"pane_id": "w1:pX", "rect": {"x": 0, "y": 50}},
                    ],
                    "splits": [],
                    "area": {"x": 0, "y": 0, "width": 200, "height": 100},
                    "zoomed": False,
                },
                "type": "pane_layout",
            },
        }
        focus_response = {
            "id": "1",
            "result": {"type": "pane_info", "pane": {"pane_id": "w1:pB"}},
        }
        fake_socket = mock.Mock()
        fake_socket.recv.return_value = (json.dumps(focus_response) + "\n").encode()
        completed = subprocess.CompletedProcess(
            args=["herdr", "pane", "layout", "--current"],
            returncode=0,
            stdout=json.dumps(layout_response),
            stderr="",
        )

        with mock.patch.object(herdr_pane_switch.subprocess, "run", return_value=completed) as run:
            with mock.patch.object(herdr_pane_switch.socket, "socket", return_value=fake_socket):
                with mock.patch.object(
                    herdr_pane_switch.select, "select", return_value=([fake_socket], [], [])
                ):
                    with mock.patch.object(herdr_pane_switch.sys, "argv", [str(SCRIPT_PATH), "2"]):
                        herdr_pane_switch.main()

        self.assertEqual(run.call_args.args[0], ["herdr", "pane", "layout", "--current"])
        request = json.loads(fake_socket.sendall.call_args.args[0].decode())
        self.assertEqual(request["params"], {"pane_id": "w1:pB"})


class CommandErrorTests(unittest.TestCase):
    def run_cli(self, herdr_output, index="1", herdr_exit=0, socket_path="/tmp/unavailable.sock"):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_herdr = pathlib.Path(temp_dir) / "herdr"
            fake_herdr.write_text(
                "#!/bin/sh\nprintf '%s' \"$HERDR_TEST_OUTPUT\"\nexit \"$HERDR_TEST_EXIT\"\n"
            )
            fake_herdr.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = temp_dir + os.pathsep + environment["PATH"]
            environment["HERDR_TEST_OUTPUT"] = herdr_output
            environment["HERDR_TEST_EXIT"] = str(herdr_exit)
            environment["HERDR_SOCKET_PATH"] = socket_path
            return subprocess.run(
                [os.environ.get("PYTHON", "python3"), str(SCRIPT_PATH), index],
                capture_output=True,
                text=True,
                env=environment,
            )

    def test_malformed_layout_json_is_visible_and_nonzero(self):
        result = self.run_cli("not-json")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("malformed JSON", result.stderr)

    def test_missing_caller_context_is_visible_and_nonzero(self):
        response = json.dumps(
            {
                "id": "cli:pane:layout",
                "error": {
                    "code": "missing_caller_context",
                    "message": "current pane context is required",
                },
            }
        )

        result = self.run_cli(response, herdr_exit=1)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing_caller_context", result.stderr)
        self.assertIn("current pane context is required", result.stderr)

    def test_out_of_range_index_is_visible_and_nonzero(self):
        response = json.dumps(
            {
                "id": "cli:pane:layout",
                "result": {
                    "layout": {
                        "workspace_id": "w1",
                        "panes": [{"pane_id": "w1:p1", "rect": {"x": 0, "y": 0}}],
                    }
                },
            }
        )

        result = self.run_cli(response, index="2")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pane 2 out of range", result.stderr)

    def test_unavailable_socket_is_visible_and_nonzero(self):
        response = json.dumps(
            {
                "id": "cli:pane:layout",
                "result": {
                    "layout": {
                        "workspace_id": "w1",
                        "panes": [{"pane_id": "w1:p1", "rect": {"x": 0, "y": 0}}],
                    }
                },
            }
        )

        result = self.run_cli(response, socket_path="/tmp/herdr-recipes-does-not-exist.sock")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Herdr pane.focus socket", result.stderr)


if __name__ == "__main__":
    unittest.main()
