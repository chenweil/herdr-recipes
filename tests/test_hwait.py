import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts" / "hwait.sh"

FAKE = '''#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
with open(os.environ["HWAIT_FAKE_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")

state_path = os.environ["HWAIT_FAKE_STATE"]
state = json.load(open(state_path)) if os.path.exists(state_path) else {"queues": {}, "counts": {}}
key = " ".join(args[:2])
queue = state.get("queues", {}).get(key, [])
index = state.get("counts", {}).get(key, 0)
state.setdefault("counts", {})[key] = index + 1
with open(state_path, "w") as handle:
    json.dump(state, handle)

if queue:
    item = queue[min(index, len(queue) - 1)]
elif key == "agent get":
    item = {"exit": 0, "stdout": json.dumps({"result": {"agent": {
        "pane_id": "w1:p1", "name": "dev", "agent_status": "working"}}})}
else:
    item = {"exit": 0, "stdout": json.dumps({"result": {}})}
sys.stdout.write(item.get("stdout", ""))
sys.stderr.write(item.get("stderr", ""))
sys.exit(item.get("exit", 0))
'''


def ok(result):
    return {"exit": 0, "stdout": json.dumps({"result": result})}


def fail(code, message="fake diagnostic"):
    return {"exit": 1, "stderr": json.dumps({"error": {"code": code, "message": message}})}


def settled(state):
    return ok({"agent": {"agent_status": state, "pane_id": "w1:p1", "name": "dev"}})


class HwaitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        folder = Path(self.directory.name)
        fake = folder / "herdr"
        fake.write_text(FAKE)
        fake.chmod(0o755)
        self.state_path = folder / "state.json"
        self.log_path = folder / "calls.jsonl"
        self.env = os.environ.copy()
        self.env.update(PATH=str(folder) + os.pathsep + self.env["PATH"],
                        HWAIT_FAKE_STATE=str(self.state_path),
                        HWAIT_FAKE_LOG=str(self.log_path))

    def tearDown(self):
        self.directory.cleanup()

    def queues(self, **queues):
        self.state_path.write_text(json.dumps({"queues": queues, "counts": {}}))

    def run_hwait(self, *args):
        return subprocess.run(["bash", str(ENTRY), *args], env=self.env,
                              capture_output=True, text=True, cwd=ROOT)

    def calls(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def keys(self):
        return [" ".join(call[:2]) for call in self.calls()]

    def option(self, call, name):
        return call[call.index(name) + 1] if name in call else None

    # --- verdicts -------------------------------------------------------

    def test_marker_hit_returns_marker_and_zero_exit(self):
        self.queues(**{"pane wait-output": [ok({"matched_line": "DONE T08", "pane_id": "w1:p1"})]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verdict=marker", result.stdout)
        self.assertIn("DONE T08", result.stdout)

    def test_settled_without_marker_is_not_success(self):
        self.queues(**{"pane wait-output": [fail("timeout")], "agent wait": [settled("idle")]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08")
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertIn("verdict=settled", result.stdout)
        self.assertIn("agent read", result.stdout)

    def test_blocked_state_is_reported_as_blocked(self):
        self.queues(**{"pane wait-output": [fail("timeout")], "agent wait": [settled("blocked")]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08")
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn("verdict=blocked", result.stdout)

    def test_unknown_state_is_reported_as_unknown(self):
        self.queues(**{"pane wait-output": [fail("timeout")], "agent wait": [settled("unknown")]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08")
        self.assertEqual(result.returncode, 14, result.stderr)
        self.assertIn("verdict=unknown", result.stdout)

    def test_dead_agent_is_detected_instead_of_waiting_for_the_marker(self):
        self.queues(**{"pane wait-output": [fail("timeout")],
                       "agent wait": [fail("agent_not_found", "agent target dev not found")]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08")
        self.assertEqual(result.returncode, 12, result.stderr)
        self.assertIn("verdict=gone", result.stdout)

    def test_already_dead_agent_fails_before_any_marker_wait(self):
        self.queues(**{"agent get": [fail("agent_not_found", "agent target dev not found")]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08")
        self.assertEqual(result.returncode, 12, result.stderr)
        self.assertNotIn("pane wait-output", self.keys())

    def test_invalid_marker_response_is_an_error(self):
        self.queues(**{"pane wait-output": [{"exit": 1, "stderr": "broken output"}]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08")
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid JSON", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_invalid_lifecycle_response_is_an_error(self):
        self.queues(**{"pane wait-output": [fail("timeout")],
                       "agent wait": [{"exit": 1, "stderr": "broken output"}]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08")
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid JSON", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_overall_timeout_is_reported_as_timeout(self):
        self.queues(**{"pane wait-output": [fail("timeout")], "agent wait": [fail("timeout")]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08", "--timeout", "1")
        self.assertEqual(result.returncode, 13, result.stderr)
        self.assertIn("verdict=timeout", result.stdout)
        for call in self.calls():
            if call[:2] == ["pane", "wait-output"]:
                self.assertLessEqual(int(self.option(call, "--timeout")), 1)
            if call[:2] == ["agent", "wait"]:
                self.assertLessEqual(int(self.option(call, "--timeout")), 1)

    def test_agent_and_pane_must_identify_the_same_worker(self):
        self.queues(**{"agent get": [ok({"agent": {"pane_id": "w9:p9", "name": "dev"}})]})
        result = self.run_hwait("--agent", "dev", "--pane", "w1:p1", "--marker", "DONE T08")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not w1:p1", result.stderr)
        self.assertNotIn("pane wait-output", self.keys())

    # --- loop shape -----------------------------------------------------

    def test_marker_printed_between_slices_is_still_caught(self):
        self.queues(**{"pane wait-output": [fail("timeout"), ok({"matched_line": "DONE T08"})],
                       "agent wait": [fail("timeout")]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08",
                                "--timeout", "5000", "--slice", "1000")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.keys().count("pane wait-output"), 2)
        waits = [call for call in self.calls() if call[:2] == ["agent", "wait"]]
        self.assertEqual(self.option(waits[0], "--timeout"), "1000")

    def test_marker_wait_never_uses_the_overall_budget(self):
        # A 30 minute budget must not turn into one 30 minute blind wait.
        self.queues(**{"pane wait-output": [fail("timeout")], "agent wait": [settled("idle")]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08",
                                "--timeout", "1800000", "--slice", "60000")
        self.assertEqual(result.returncode, 10, result.stderr)
        waits = [call for call in self.calls() if call[:2] == ["agent", "wait"]]
        self.assertEqual(self.option(waits[0], "--timeout"), "60000")
        for call in self.calls():
            if call[:2] == ["pane", "wait-output"]:
                self.assertEqual(self.option(call, "--timeout"), "1500")
            if call[:2] == ["agent", "wait"]:
                self.assertLessEqual(int(self.option(call, "--timeout")), 60000)

    def test_pane_only_run_resolves_the_agent_target(self):
        self.queues(**{"pane wait-output": [ok({"matched_line": "DONE T08"})]})
        result = self.run_hwait("--pane", "w1:p1", "--marker", "DONE T08")
        self.assertEqual(result.returncode, 0, result.stderr)
        waits = [call for call in self.calls() if call[:2] == ["pane", "wait-output"]]
        self.assertEqual(waits[0][2], "w1:p1")

    def test_agent_only_run_resolves_the_pane(self):
        self.queues(**{"agent get": [ok({"agent": {"pane_id": "w9:p3", "name": "dev",
                                                 "agent_status": "working"}})],
                       "pane wait-output": [ok({"matched_line": "DONE T08"})]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08")
        self.assertEqual(result.returncode, 0, result.stderr)
        waits = [call for call in self.calls() if call[:2] == ["pane", "wait-output"]]
        self.assertEqual(waits[0][2], "w9:p3")

    # --- output contract ------------------------------------------------

    def test_json_output_carries_verdict_and_next_action(self):
        self.queues(**{"pane wait-output": [ok({"matched_line": "DONE T08"})]})
        result = self.run_hwait("--agent", "dev", "--marker", "DONE T08", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["verdict"], "marker")
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["marker"], "DONE T08")
        self.assertTrue(payload["next"])

    def test_usage_errors_are_rejected_before_touching_herdr(self):
        missing = self.run_hwait("--marker", "DONE T08")
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(self.calls(), [])

        long_slice = self.run_hwait("--agent", "dev", "--marker", "DONE T08", "--slice", "60001")
        self.assertEqual(long_slice.returncode, 2)
        self.assertEqual(self.calls(), [])

        zero_marker_timeout = self.run_hwait("--agent", "dev", "--marker", "DONE T08", "--marker-timeout", "0")
        self.assertEqual(zero_marker_timeout.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_version_flag_reports_the_repository_version(self):
        result = self.run_hwait("--version")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith("hwait "))


class ProtocolDocumentTests(unittest.TestCase):
    """The waiting protocol must keep both signals and the nested delegation rule."""

    def setUp(self):
        self.prompt = (ROOT / "scripts" / "coordinator-prompt.md").read_text()
        self.skill = (ROOT / "skills" / "herdr-recipes" / "SKILL.md").read_text()

    def test_coordinator_prompt_keeps_both_signals(self):
        self.assertIn("hwait.sh", self.prompt)
        self.assertIn("agent wait", self.prompt)
        self.assertIn("pane wait-output", self.prompt)

    def test_coordinator_prompt_forbids_long_single_marker_waits(self):
        self.assertIn("分片不超过 60 秒", self.prompt)
        self.assertIn("禁止把 `--timeout` 超过 60 秒的", self.prompt)

    def test_coordinator_prompt_requires_reporting_failed_subtasks(self):
        self.assertIn("嵌套委派", self.prompt)
        self.assertIn("子任务失败必须写进给上游的回报", self.prompt)

    def test_skill_constraints_cover_waiting_and_nested_delegation(self):
        self.assertIn("`hwait.sh`", self.skill)
        self.assertIn("嵌套委派", self.skill)


if __name__ == "__main__":
    unittest.main()
