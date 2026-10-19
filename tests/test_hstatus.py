import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts" / "hstatus.sh"
SPEC = importlib.util.spec_from_file_location("hstatus", ROOT / "scripts" / "hstatus.py")
hstatus = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hstatus)

FAKE = '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STATUS_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
with open(os.environ["STATUS_RESPONSES"]) as source:
    replies = json.load(source)
key = " ".join(sys.argv[1:])
if key not in replies:
    print("forbidden or unexpected command: " + key, file=sys.stderr)
    sys.exit(91)
reply = replies[key]
sys.stdout.write(reply.get("stdout", ""))
sys.stderr.write(reply.get("stderr", ""))
sys.exit(reply.get("exit", 0))
'''


class StatusCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        folder = Path(self.directory.name)
        fake = folder / "herdr"
        fake.write_text(FAKE)
        fake.chmod(0o755)
        self.config = folder / "responses.json"
        self.log = folder / "calls.jsonl"
        self.env = os.environ.copy()
        self.env.pop("HERDR_PANE_ID", None)
        self.env.update(PATH=str(folder) + os.pathsep + self.env["PATH"],
                        STATUS_LOG=str(self.log), STATUS_RESPONSES=str(self.config))
        self.replies = {}
        self.reply("workspace list", {"workspaces": [
            {"workspace_id": "w1", "label": "demo"}, {"workspace_id": "w2", "label": "other"}]})
        # Intentionally neither creation order nor lexical order.
        self.reply("pane list --workspace w1", {"panes": [
            {"pane_id": "w1:pB", "workspace_id": "w1", "label": "shell"},
            {"pane_id": "w1:p2", "workspace_id": "w1", "label": "Review", "agent": "pi", "agent_status": "idle"},
            {"pane_id": "w1:p1", "workspace_id": "w1", "label": "实现", "agent_status": "working"}]})
        self.reply("agent list", {"agents": [
            {"pane_id": "w2:p1", "workspace_id": "w2", "agent": "claude", "name": "foreign", "agent_status": "working"},
            {"pane_id": "w1:p1", "workspace_id": "w1", "agent": "codex", "name": "implement", "agent_status": "blocked"},
            {"pane_id": "w1:p2", "workspace_id": "w1", "agent": "pi", "name": "review", "agent_status": "idle"}]})
        self.reply("pane layout --pane w1:p1", {"layout": {
            "workspace_id": "w1", "panes": [
                {"pane_id": "w1:p1", "rect": {"x": 60, "y": 0}},
                {"pane_id": "w1:p2", "rect": {"x": 0, "y": 0}},
                {"pane_id": "w1:pB", "rect": {"x": 0, "y": 20}}]}})
        self.reply("pane current --current", {"pane": {"pane_id": "w1:p2", "workspace_id": "w1"}})

    def tearDown(self):
        self.directory.cleanup()

    def reply(self, command, result):
        self.replies[command] = {"stdout": json.dumps({"result": result})}

    def run_status(self, *args, **env):
        self.config.write_text(json.dumps(self.replies))
        return subprocess.run(["bash", str(ENTRY), *args], env={**self.env, **env},
                              capture_output=True, text=True, cwd=ROOT)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_failure(self, result, message):
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn(message, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_explicit_json_is_scoped_complete_and_sorted_by_geometry(self):
        result = self.run_status("--workspace", "w1", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "schema_version": 1, "workspace": {"workspace_id": "w1", "label": "demo"},
            "pane_order": "geometry", "panes": [
                {"pane_id": "w1:p2", "label": "Review", "kind": "pi", "name": "review", "status": "idle"},
                {"pane_id": "w1:p1", "label": "实现", "kind": "codex", "name": "implement", "status": "blocked"},
                {"pane_id": "w1:pB", "label": "shell", "kind": None, "name": None, "status": "unknown"}]})
        self.assertEqual(self.calls(), [["workspace", "list"], ["pane", "list", "--workspace", "w1"],
                                      ["agent", "list"], ["pane", "layout", "--pane", "w1:p1"]])
        self.assertEqual(len(result.stdout.splitlines()), 1)

    def test_human_output_is_one_line_per_pane_and_does_not_imply_completion(self):
        result = self.run_status("--workspace", "w1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'ws=w1 label="demo" panes=3 order=geometry\n'
                         '  w1:p2 label="Review" kind=pi name=review status=idle\n'
                         '  w1:p1 label="实现" kind=codex name=implement status=blocked\n'
                         '  w1:pB label="shell" kind=- name=- status=unknown\n')
        self.assertEqual(result.stderr, "")

    def test_current_uses_resolved_caller_workspace_instead_of_environment_or_focus(self):
        result = self.run_status("--json", HERDR_PANE_ID="old-pane", HERDR_WORKSPACE_ID="w2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["workspace"]["workspace_id"], "w1")
        self.assertEqual(self.calls()[0], ["pane", "current", "--current"])
        self.assertEqual(self.calls().count(["agent", "list"]), 1)

    def test_missing_caller_context_is_visible_without_any_query(self):
        self.assert_failure(self.run_status("--json"), "--workspace")
        self.assertEqual(self.calls(), [])

    def test_explicit_workspace_ignores_invalid_caller_context(self):
        result = self.run_status("--workspace", "w1", "--json", HERDR_PANE_ID="nonexistent")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(["pane", "current", "--current"], self.calls())

    def test_missing_workspace_is_not_an_empty_successful_snapshot(self):
        self.assert_failure(self.run_status("--workspace", "missing", "--json"), "workspace not found")
        self.assertEqual(self.calls(), [["workspace", "list"]])

    def test_absent_optional_fields_unknown_status_and_extra_fields_are_preserved_safely(self):
        self.reply("workspace list", {"workspaces": [{"workspace_id": "w1"}]})
        self.reply("pane list --workspace w1", {"panes": [{"pane_id": "w1:p1", "future_field": True}]})
        self.reply("agent list", {"agents": [{"pane_id": "w1:p1", "agent_status": "future-wait", "extra": 4}]})
        self.reply("pane layout --pane w1:p1", {"layout": {"workspace_id": "w1", "panes": [
            {"pane_id": "w1:p1", "rect": {"x": 0, "y": 0}}]}})
        result = self.run_status("--workspace", "w1", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIsNone(payload["workspace"]["label"])
        self.assertEqual(payload["panes"], [{"pane_id": "w1:p1", "label": None, "kind": None,
                                            "name": None, "status": "future-wait"}])

    def test_empty_workspace_queries_agents_once_and_skips_layout(self):
        self.reply("pane list --workspace w1", {"panes": []})
        result = self.run_status("--workspace", "w1", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["panes"], [])
        self.assertEqual(self.calls().count(["agent", "list"]), 1)
        self.assertFalse(any(call[:2] == ["pane", "layout"] for call in self.calls()))

    def test_foreign_workspace_agent_cannot_override_a_selected_pane(self):
        self.reply("agent list", {"agents": [{"pane_id": "w1:p1", "workspace_id": "w2",
                                              "agent": "claude", "name": "foreign"}]})
        result = self.run_status("--workspace", "w1", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        pane = next(p for p in json.loads(result.stdout)["panes"] if p["pane_id"] == "w1:p1")
        self.assertIsNone(pane["kind"])
        self.assertIsNone(pane["name"])
        self.assertEqual(pane["status"], "working")

    def test_optional_layout_failure_warns_and_falls_back_to_exact_id_order(self):
        self.replies["pane layout --pane w1:p1"] = {"exit": 1, "stderr": "layout unavailable"}
        result = self.run_status("--workspace", "w1", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["pane_order"], "pane_id")
        self.assertEqual([p["pane_id"] for p in payload["panes"]], ["w1:p1", "w1:p2", "w1:pB"])
        self.assertIn("using exact pane_id order", result.stderr)

    def test_partial_multitab_wrong_workspace_and_bad_geometry_use_one_fallback(self):
        layouts = [
            {"workspace_id": "w1", "panes": [{"pane_id": "w1:p1", "rect": {"x": 0, "y": 0}}]},
            {"workspace_id": "w2", "panes": []},
        ]
        original = json.loads(self.replies["pane layout --pane w1:p1"]["stdout"])["result"]["layout"]
        for bad in (True, "0", float("nan"), float("inf"), 10**400, None):
            layout = json.loads(json.dumps(original))
            layout["panes"][0]["rect"]["x"] = bad
            layouts.append(layout)
        for layout in layouts:
            with self.subTest(layout=layout):
                self.reply("pane layout --pane w1:p1", {"layout": layout})
                result = self.run_status("--workspace", "w1", "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["pane_order"], "pane_id")
                self.assertIn("using exact pane_id order", result.stderr)

    def test_malformed_mandatory_responses_fail_without_partial_stdout(self):
        originals = self.replies.copy()
        for command, response in (
            ("workspace list", {"stdout": "{"}),
            ("workspace list", {"stdout": "[]"}),
            ("pane list --workspace w1", {"stdout": '{"result":{"panes":{}}}'}),
            ("pane list --workspace w1", {"stdout": '{"result":{"panes":[{}]}}'}),
            ("agent list", {"stdout": '{"result":{"agents":[{"pane_id":"a"},{"pane_id":"a"}]}}'}),
            ("agent list", {"stdout": '{"error":{"code":"broken"}}'}),
        ):
            with self.subTest(command=command, response=response):
                self.replies = {**originals, command: response}
                result = self.run_status("--workspace", "w1", "--json")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.startswith("hstatus:"))
                self.assertNotIn("Traceback", result.stderr)

    def test_unavailable_server_and_caller_failure_have_visible_nonzero_errors(self):
        self.replies["workspace list"] = {"exit": 1, "stderr": "connection refused"}
        self.assert_failure(self.run_status("--workspace", "w1", "--json"), "connection refused")
        self.replies["pane current --current"] = {"exit": 1, "stderr": "caller pane not found"}
        self.assert_failure(self.run_status("--json", HERDR_PANE_ID="gone"), "caller pane not found")

    def test_labels_are_not_executed_and_human_control_characters_are_escaped(self):
        self.reply("workspace list", {"workspaces": [{"workspace_id": "w1", "label": "line\n\x1b[31m"}]})
        result = self.run_status("--workspace", "w1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(r'line\n\u001b[31m', result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), 4)

    def test_help_version_and_argument_errors_never_query_herdr(self):
        for flag in ("-v", "-V", "--version"):
            with self.subTest(flag=flag):
                result = self.run_status(flag)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"hstatus {(ROOT / 'VERSION').read_text().strip()}\n")
        result = self.run_status("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--workspace", result.stdout)
        installed = Path(self.directory.name) / "installed-scripts"
        installed.symlink_to(ROOT / "scripts", target_is_directory=True)
        linked = subprocess.run(["bash", str(installed / "hstatus.sh"), "--version"],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(linked.returncode, 0, linked.stderr)
        self.assertEqual(linked.stdout, f"hstatus {(ROOT / 'VERSION').read_text().strip()}\n")
        for args in (("--workspace",), ("--workspace", ""), ("--watch", "5")):
            result = self.run_status(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
        self.assertEqual(self.calls(), [])


class QueryFailureTests(unittest.TestCase):
    def test_timeout_missing_executable_and_invalid_encoding_are_user_visible(self):
        for failure in (FileNotFoundError("not installed"), subprocess.TimeoutExpired("herdr", 5),
                        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad encoding")):
            with self.subTest(failure=failure):
                with mock.patch.object(hstatus.subprocess, "run", side_effect=failure):
                    with self.assertRaises(hstatus.StatusError):
                        hstatus.query("workspace", "list")


if __name__ == "__main__":
    unittest.main()
