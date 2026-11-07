"""T5 — state on disk and ``--resume``.

The state file is what makes a run survivable: a killed supervisor must be
able to continue the same run without re-dispatching the steps that already
finished.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hsuper  # noqa: E402


FLOW = """
[flow.sample]
description = "测试"

[flow.sample.defaults]
kind = "codex"

[flow.sample.steps.dev]
role = "实现"
prompt = "任务：{{task}}\\n打印 {{marker}}"
expect = ["{{run_dir}}/result/dev.json"]

[flow.sample.steps.review]
role = "审查"
prompt = "审查 {{step}}"
expect = ["{{run_dir}}/result/review.json"]
"""


class StateFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_state_is_written_atomically(self):
        path = self.folder / "state.json"
        state = hsuper.State.create(path, "r1", "sample", "任务", self.folder)
        state.update_step("dev", status="running")
        # No .tmp file is left behind.
        leftovers = [p.name for p in self.folder.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        data = json.loads(path.read_text())
        self.assertEqual(data["steps"]["dev"]["status"], "running")

    def test_state_records_the_run_fields(self):
        path = self.folder / "state.json"
        hsuper.State.create(path, "r1", "sample", "任务", self.folder)
        data = json.loads(path.read_text())
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["run_id"], "r1")
        self.assertEqual(data["flow"], "sample")
        self.assertEqual(data["task"], "任务")
        self.assertEqual(data["steps"], {})
        self.assertEqual(data["deviations"], [])

    def test_deviations_are_recorded(self):
        path = self.folder / "state.json"
        state = hsuper.State.create(path, "r1", "sample", "任务", self.folder)
        state.record_deviation("dev", "restart_new_pane", "old pane busy")
        data = json.loads(path.read_text())
        self.assertEqual(len(data["deviations"]), 1)
        self.assertEqual(data["deviations"][0]["kind"], "restart_new_pane")

    def test_a_broken_state_file_is_reported(self):
        path = self.folder / "state.json"
        path.write_text("{ broken", encoding="utf-8")
        with self.assertRaises(hsuper.UsageError):
            hsuper.State.load(path)

    def test_run_id_is_herdr_safe(self):
        for _ in range(20):
            run_id = hsuper.new_run_id()
            self.assertRegex(run_id, r"^[a-z0-9]+$")
            self.assertLessEqual(len(run_id), 32)

    def test_run_dir_name_carries_the_run_id(self):
        name = hsuper.run_dir_name("r1")
        self.assertTrue(name.endswith("-r1"))
        self.assertRegex(name, r"^\d{8}-\d{4}-")

    def test_write_text_atomic_leaves_no_temp_file(self):
        path = self.folder / "out.md"
        hsuper.write_text_atomic(path, "hello")
        self.assertEqual(path.read_text(), "hello")
        self.assertEqual([p.name for p in self.folder.iterdir() if p.name.endswith(".tmp")], [])


class ResumeSelectionTests(unittest.TestCase):
    """Which steps a resume re-runs."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.path = self.folder / "state.json"
        self.state = hsuper.State.create(self.path, "r1", "sample", "任务", self.folder)
        for step_id in ("dev", "review", "commit"):
            self.state.update_step(step_id, status="pending")

    def tearDown(self):
        self.directory.cleanup()

    def supervisor(self):
        supervisor = hsuper.Supervisor.__new__(hsuper.Supervisor)
        supervisor.state = self.state
        supervisor.flow = {"steps": {}}
        supervisor.settings_cache = {
            "dev": {"id": "dev", "next": ["review"]},
            "review": {"id": "review", "next": ["commit"]},
            "commit": {"id": "commit", "next": []},
        }
        return supervisor

    def test_pending_steps_are_re_run(self):
        supervisor = self.supervisor()
        self.assertEqual(supervisor.resume_order(["dev", "review", "commit"]),
                         ["dev", "review", "commit"])

    def test_done_steps_are_not_re_run(self):
        self.state.update_step("dev", status="done")
        supervisor = self.supervisor()
        selected = supervisor.resume_order(["dev", "review", "commit"])
        self.assertNotIn("dev", selected)

    def test_a_step_whose_dependency_is_re_run_is_re_run_too(self):
        # The selection is closed under the dependency relation: a dependency
        # is dispatched first and must finish before its dependent runs.
        self.state.update_step("dev", status="running")
        self.state.update_step("review", status="failed")
        supervisor = self.supervisor()
        selected = supervisor.resume_order(["dev", "review", "commit"])
        self.assertIn("dev", selected)
        self.assertIn("review", selected)
        self.assertIn("commit", selected)

    def test_a_dependent_of_a_done_step_is_re_run(self):
        self.state.update_step("dev", status="done")
        supervisor = self.supervisor()
        selected = supervisor.resume_order(["dev", "review", "commit"])
        self.assertNotIn("dev", selected)
        self.assertIn("review", selected)

    def test_blocked_steps_keep_their_state(self):
        self.state.update_step("dev", status="blocked")
        supervisor = self.supervisor()
        selected = supervisor.resume_order(["dev", "review", "commit"])
        self.assertNotIn("dev", selected)

    def test_failed_steps_are_re_run(self):
        self.state.update_step("dev", status="failed")
        supervisor = self.supervisor()
        self.assertIn("dev", supervisor.resume_order(["dev", "review", "commit"]))

    def test_skipped_steps_are_re_run(self):
        # A skipped step never ran, so it still owes the run its work: if it
        # were not re-run the flow would stay unfinished forever while the
        # exit code claimed success.
        self.state.update_step("dev", status="skipped")
        supervisor = self.supervisor()
        self.assertIn("dev", supervisor.resume_order(["dev", "review", "commit"]))

    def test_a_dependent_of_a_skipped_step_is_re_run_too(self):
        self.state.update_step("dev", status="failed")
        self.state.update_step("review", status="skipped")
        supervisor = self.supervisor()
        selected = supervisor.resume_order(["dev", "review", "commit"])
        self.assertIn("dev", selected)
        self.assertIn("review", selected)


class ExitCodeTests(unittest.TestCase):
    """The exit code table.

    One table, published in three places: the proposal's §8.1,
    flows/_schema.md and scripts/README.md.  These tests pin the mapping
    the implementation has to honour.
    """

    def supervisor(self, budget_truncated=False):
        supervisor = hsuper.Supervisor.__new__(hsuper.Supervisor)
        supervisor.budget_truncated = budget_truncated
        return supervisor

    def test_a_failed_branch_is_20(self):
        self.assertEqual(self.supervisor().exit_code(["dev"], [], []), 20)

    def test_a_blocked_branch_is_21(self):
        self.assertEqual(self.supervisor().exit_code([], ["dev"], []), 21)

    def test_a_truncated_budget_is_22(self):
        self.assertEqual(self.supervisor(True).exit_code([], [], ["dev"]), 22)

    def test_skipped_without_a_failure_is_23(self):
        self.assertEqual(self.supervisor().exit_code([], [], ["dev"]), 23)

    def test_nothing_left_over_is_0(self):
        self.assertEqual(self.supervisor().exit_code([], [], []), 0)

    def test_a_failure_wins_over_everything_else(self):
        # ADR-0004: a failed branch is never packaged as a partial success.
        self.assertEqual(self.supervisor(True).exit_code(["dev"], ["review"],
                                                         ["commit"]), 20)

    def test_a_block_wins_over_the_budget(self):
        self.assertEqual(self.supervisor(True).exit_code([], ["dev"], ["review"]),
                         21)

    def test_the_budget_wins_over_a_partial_success(self):
        self.assertEqual(self.supervisor(True).exit_code([], [], ["dev"]), 22)


class ResumeCommandTests(unittest.TestCase):
    """The --resume command line."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_resume_without_a_run_is_rejected(self):
        entry = SCRIPTS / "hsuper.py"
        result = subprocess.run(
            [sys.executable, str(entry), "--resume", "--path", str(self.folder)],
            capture_output=True, text=True, cwd=str(self.folder))
        self.assertNotEqual(result.returncode, 0)

    def test_resume_of_an_unknown_run_is_reported(self):
        entry = SCRIPTS / "hsuper.py"
        result = subprocess.run(
            [sys.executable, str(entry), "--run", "zzzz", "--resume",
             "--path", str(self.folder)],
            capture_output=True, text=True, cwd=str(self.folder))
        self.assertEqual(result.returncode, 1)
        self.assertIn("找不到 run", result.stderr)

    def test_run_without_resume_is_rejected(self):
        entry = SCRIPTS / "hsuper.py"
        runs = self.folder / ".herdr-recipes" / "runs" / "20260101-0000-r1"
        runs.mkdir(parents=True)
        (runs / "state.json").write_text(json.dumps({
            "schema_version": 1, "run_id": "r1", "flow": "sample",
            "task": "任务", "cwd": str(self.folder), "steps": {},
            "deviations": []}), encoding="utf-8")
        (runs / "flow.toml").write_text(FLOW, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(entry), "--run", "r1", "--path", str(self.folder)],
            capture_output=True, text=True, cwd=str(self.folder))
        self.assertEqual(result.returncode, 1)
        self.assertIn("--resume", result.stderr)

    def test_resume_reads_the_flow_from_the_run_directory(self):
        entry = SCRIPTS / "hsuper.py"
        runs = self.folder / ".herdr-recipes" / "runs" / "20260101-0000-r1"
        runs.mkdir(parents=True)
        (runs / "state.json").write_text(json.dumps({
            "schema_version": 1, "run_id": "r1", "flow": "sample",
            "task": "任务", "cwd": str(self.folder),
            "steps": {"dev": {"status": "done"}}, "deviations": []}),
            encoding="utf-8")
        (runs / "flow.toml").write_text(FLOW, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(entry), "--run", "r1", "--resume", "--dry-run",
             "--path", str(self.folder)],
            capture_output=True, text=True, cwd=str(self.folder))
        # The dry run still validates the flow that the run stored.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dev", result.stdout)

    def test_a_run_without_flow_toml_cannot_resume(self):
        entry = SCRIPTS / "hsuper.py"
        runs = self.folder / ".herdr-recipes" / "runs" / "20260101-0000-r1"
        runs.mkdir(parents=True)
        (runs / "state.json").write_text(json.dumps({
            "schema_version": 1, "run_id": "r1", "flow": "sample",
            "task": "任务", "cwd": str(self.folder), "steps": {}}),
            encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(entry), "--run", "r1", "--resume",
             "--path", str(self.folder)],
            capture_output=True, text=True, cwd=str(self.folder))
        self.assertEqual(result.returncode, 1)
        self.assertIn("flow.toml", result.stderr)


if __name__ == "__main__":
    unittest.main()


class SupervisorTeeTests(unittest.TestCase):
    """--in-pane mirrors pane output into logs/supervisor.log."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_output_is_mirrored_to_the_log_and_restored_after(self):
        import io
        log = self.folder / "logs" / "supervisor.log"
        buffer = io.StringIO()
        real_stdout, real_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = buffer, buffer
        try:
            supervisor = hsuper.Supervisor.__new__(hsuper.Supervisor)
            supervisor.attach_tee(log)
            print("pane line")
            print("stderr line", file=sys.stderr)
            supervisor.detach_tee()
        finally:
            sys.stdout, sys.stderr = real_stdout, real_stderr
        mirrored = log.read_text()
        self.assertIn("pane line", mirrored)
        self.assertIn("stderr line", mirrored)
        # The pane still saw every line exactly once.
        self.assertEqual(buffer.getvalue(), "pane line\nstderr line\n")

    def test_unwritable_log_file_is_survived(self):
        import io
        buffer = io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = buffer
        try:
            supervisor = hsuper.Supervisor.__new__(hsuper.Supervisor)
            supervisor.attach_tee(self.folder / "no-such-dir" / "supervisor.log")
            print("still prints")
        finally:
            sys.stdout = real_stdout
        self.assertIn("still prints", buffer.getvalue())
