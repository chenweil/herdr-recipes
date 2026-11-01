"""T2 — template rendering, prompt files, and the marker echo trap.

The marker must reach the worker (it is the completion signal) but must never
reach the terminal through the supervisor's own commands: a marker that shows
up on screen — including inside a command line — makes ``pane wait-output``
match before the work is done.
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


class RenderTemplateTests(unittest.TestCase):
    def test_documented_placeholders_are_expanded(self):
        rendered = hsuper.render_template(
            "{{task}}|{{run_id}}|{{step}}|{{marker}}|{{run_dir}}",
            {"task": "T", "run_id": "r1", "step": "dev", "marker": "M",
             "run_dir": "/tmp/run"})
        self.assertEqual(rendered, "T|r1|dev|M|/tmp/run")

    def test_unknown_placeholder_is_rejected(self):
        with self.assertRaises(hsuper.FlowError):
            hsuper.render_template("{{taskk}}", {"task": "T"})

    def test_near_miss_placeholder_is_rejected(self):
        # A space or a wrong case must fail loudly, not expand to nothing.
        for template in ("{{task }}", "{{TASK}}", "{{ task}}"):
            with self.subTest(template=template):
                with self.assertRaises(hsuper.FlowError):
                    hsuper.render_template(template, {"task": "T"})

    def test_missing_context_value_becomes_empty(self):
        self.assertEqual(hsuper.render_template("[{{task}}]", {}), "[]")

    def test_marker_shape_is_short_and_unambiguous(self):
        marker = hsuper.marker_for("r7f3", "dev")
        self.assertEqual(marker, "DONE r7f3-dev")
        # A long marker could be split by the terminal's line wrapping.
        self.assertLess(len(marker), 32)


class PromptFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def settings(self, **overrides):
        base = {
            "id": "dev",
            "role": "实现",
            "kind": "codex",
            "model": "",
            "args": [],
            "prompt": "任务：{{task}}\n打印 {{marker}}",
            "prompt_file": None,
            "next": [],
            "expect": [],
            "gate": "none",
            "timeout_ms": 1000,
            "retries": 2,
            "rebuilds": 1,
            "cwd": "",
        }
        base.update(overrides)
        return base

    def context(self):
        return {"task": "实现登录", "run_id": "r7f3", "step": "dev",
                "marker": hsuper.marker_for("r7f3", "dev")}

    def test_prompt_file_contains_the_marker(self):
        text = hsuper.build_prompt(self.settings(), self.context(), self.run_dir)
        self.assertIn("DONE r7f3-dev", text)
        self.assertIn("实现登录", text)

    def test_submitted_text_never_contains_the_marker(self):
        """The submission is the only thing that reaches the terminal."""
        text = hsuper.submission_text(self.run_dir, "dev")
        self.assertNotIn("DONE r7f3-dev", text)
        self.assertNotIn("r7f3-dev", text)
        self.assertIn("prompts", text)
        self.assertIn("dev.md", text)

    def test_prompt_file_can_be_written_and_read_back(self):
        supervisor = hsuper.Supervisor.__new__(hsuper.Supervisor)
        supervisor.run_dir = self.run_dir
        supervisor.run_id = "r7f3"
        supervisor.task = "实现登录"
        supervisor.settings_cache = {}
        supervisor.flow = {"steps": {"dev": {"prompt": "任务：{{task}}"}}}
        path, text = supervisor.write_prompt_file("dev", self.settings())
        self.assertTrue(path.is_file())
        self.assertEqual(path, self.run_dir / "prompts" / "dev.md")
        self.assertIn("DONE r7f3-dev", path.read_text())

    def test_prompt_file_source_is_read_from_disk(self):
        source = self.run_dir / "source.md"
        source.write_text("来自文件 {{task}} {{marker}}", encoding="utf-8")
        settings = self.settings(prompt=None, prompt_file=str(source))
        text = hsuper.build_prompt(settings, self.context(), self.run_dir)
        self.assertIn("来自文件", text)
        self.assertIn("DONE r7f3-dev", text)

    def test_missing_prompt_file_is_reported(self):
        settings = self.settings(prompt=None, prompt_file="does/not/exist.md")
        with self.assertRaises(hsuper.FlowError):
            hsuper.build_prompt(settings, self.context(), self.run_dir)


class SubmissionTextTests(unittest.TestCase):
    def test_submission_points_at_the_prompt_file(self):
        text = hsuper.submission_text(Path("/tmp/run-1"), "review")
        self.assertIn("prompts/review.md", text)
        self.assertTrue(text.startswith("读取"))

    def test_submission_has_no_marker_for_any_step(self):
        for step_id in ("dev", "review", "commit"):
            text = hsuper.submission_text(Path("/tmp/run-1"), step_id)
            self.assertNotIn("DONE", text)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.directory.name)
        (self.run_dir / "result").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.directory.cleanup()

    def settings(self, **overrides):
        base = {
            "id": "dev", "role": "实现", "kind": "codex", "model": "", "args": [],
            "prompt": "x", "prompt_file": None, "next": [], "expect": [],
            "gate": "artifact", "timeout_ms": 1000, "retries": 2, "rebuilds": 1,
            "cwd": "",
        }
        base.update(overrides)
        return base

    def write_result(self, payload):
        (self.run_dir / "result" / "dev.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def test_gate_needs_the_result_file(self):
        self.assertFalse(hsuper.gate_ok(self.settings(), self.run_dir, self.run_dir))

    def test_gate_needs_a_parseable_result_file(self):
        (self.run_dir / "result" / "dev.json").write_text("{ broken", encoding="utf-8")
        self.assertFalse(hsuper.gate_ok(self.settings(), self.run_dir, self.run_dir))

    def test_gate_passes_with_a_done_result(self):
        self.write_result({"status": "done", "summary": "ok"})
        self.assertTrue(hsuper.gate_ok(self.settings(), self.run_dir, self.run_dir))

    def test_gate_fails_on_failed_result(self):
        self.write_result({"status": "failed"})
        self.assertFalse(hsuper.gate_ok(self.settings(), self.run_dir, self.run_dir))

    def test_gate_none_skips_the_evidence_check(self):
        self.assertTrue(hsuper.gate_ok(self.settings(gate="none"),
                                       self.run_dir, self.run_dir))

    def test_gate_checks_expect_paths(self):
        settings = self.settings(expect=["artifact.txt"])
        self.write_result({"status": "done"})
        self.assertFalse(hsuper.gate_ok(settings, self.run_dir, self.run_dir))
        (self.run_dir / "artifact.txt").write_text("x", encoding="utf-8")
        self.assertTrue(hsuper.gate_ok(settings, self.run_dir, self.run_dir))

    def test_gate_accepts_a_glob_expect(self):
        settings = self.settings(expect=["*.json"])
        self.write_result({"status": "done"})
        (self.run_dir / "artifact.json").write_text("{}", encoding="utf-8")
        self.assertTrue(hsuper.gate_ok(settings, self.run_dir, self.run_dir))

    def test_result_status_reads_the_status_field(self):
        self.write_result({"status": "done"})
        status, payload = hsuper.result_status(self.run_dir, "dev")
        self.assertEqual(status, "done")
        self.assertEqual(payload["status"], "done")

    def test_result_status_of_a_missing_file_is_none(self):
        status, payload = hsuper.result_status(self.run_dir, "ghost")
        self.assertIsNone(status)
        self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
