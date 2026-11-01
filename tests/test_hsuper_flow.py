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


VALID = """
[flow.sample]
description = "测试流程"
max_parallel = 2

[flow.sample.defaults]
kind = "codex"
model = ""

[flow.sample.steps.dev]
role = "实现"
prompt = "做 {{task}}"
next = ["review"]

[flow.sample.steps.review]
role = "审查"
prompt = "审查 {{run_id}} {{step}} {{marker}} {{run_dir}}"
"""


def write_flow(directory, text, name="sample"):
    path = Path(directory) / ("%s.toml" % name)
    path.write_text(text, encoding="utf-8")
    return path


class FlowLoadingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_valid_flow_loads(self):
        path = write_flow(self.folder, VALID)
        name, flow, _ = hsuper.load_flow(path)
        self.assertEqual(name, "sample")
        self.assertEqual(flow["max_parallel"], 2)
        self.assertIn("dev", flow["steps"])

    def test_missing_file_is_reported(self):
        with self.assertRaises(hsuper.FlowError):
            hsuper.load_flow(self.folder / "nope.toml")

    def test_flow_without_table_is_rejected(self):
        path = write_flow(self.folder, "[other]\nx = 1\n")
        with self.assertRaises(hsuper.FlowError):
            hsuper.load_flow(path)

    def test_two_flows_in_one_file_are_rejected(self):
        path = write_flow(self.folder, "[flow.a]\n[flow.b]\n")
        with self.assertRaises(hsuper.FlowError):
            hsuper.load_flow(path)


class ValidationTests(unittest.TestCase):
    """§7.2 — all eight rules must be enforced."""

    def validate(self, text, **kwargs):
        path = write_flow(self.folder, text)
        _, flow, _ = hsuper.load_flow(path)
        return hsuper.validate_flow(flow, "sample", **kwargs)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    # rule 1 — next must point at an existing step
    def test_rule1_next_must_exist(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt = "x"
next = ["ghost"]
""")
        self.assertTrue(any("ghost" in e for e in errors), errors)

    # rule 2 — no cycles
    def test_rule2_cycle_is_detected(self):
        errors = self.validate("""
[flow.sample.steps.a]
prompt = "x"
next = ["b"]

[flow.sample.steps.b]
prompt = "x"
next = ["a"]
""")
        self.assertTrue(any("cycle detected" in e for e in errors), errors)
        self.assertTrue(any("a -> b -> a" in e for e in errors), errors)

    def test_self_cycle_is_detected(self):
        errors = self.validate("""
[flow.sample.steps.a]
prompt = "x"
next = ["a"]
""")
        self.assertTrue(any("cycle detected" in e for e in errors), errors)

    # rule 3 — every step must be reachable from start
    def test_rule3_unreachable_step(self):
        # `next` names the successors, so without an explicit start every
        # component has a root.  An explicit `start` is what can leave a
        # step unreachable.
        errors = self.validate("""
[flow.sample]
start = ["a"]

[flow.sample.steps.a]
prompt = "x"

[flow.sample.steps.orphan]
prompt = "x"
next = ["a"]
""")
        self.assertTrue(any("unreachable step: orphan" in e for e in errors),
                        errors)

    def test_rule3_isolated_cycle_is_reported(self):
        errors = self.validate("""
[flow.sample.steps.a]
prompt = "x"
next = ["b"]

[flow.sample.steps.b]
prompt = "x"
next = ["a"]
""")
        self.assertTrue(any("cycle detected" in e for e in errors), errors)

    def test_explicit_start_defines_reachability(self):
        errors = self.validate("""
[flow.sample]
start = ["a"]

[flow.sample.steps.a]
prompt = "x"

[flow.sample.steps.b]
prompt = "x"
""")
        self.assertTrue(any("unreachable step: b" in e for e in errors), errors)

    # rule 4 — exactly one of prompt / prompt_file
    def test_rule4_prompt_required(self):
        errors = self.validate("""
[flow.sample.steps.dev]
role = "x"
""")
        self.assertTrue(any("prompt" in e for e in errors), errors)

    def test_rule4_prompt_and_prompt_file_conflict(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt = "x"
prompt_file = "y.md"
""")
        self.assertTrue(any("同时配置" in e for e in errors), errors)

    def test_rule4_prompt_file_is_accepted(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt_file = "prompts/dev.md"
""")
        self.assertEqual(errors, [])

    # rule 5 — kind must be a kind herdr supports
    def test_rule5_unknown_kind_is_rejected(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt = "x"
kind = "not-a-real-kind"
""", known_kinds={"codex", "pi", "claude"})
        self.assertTrue(any("not-a-real-kind" in e for e in errors), errors)

    def test_rule5_known_kind_is_accepted(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt = "x"
kind = "codex"
""", known_kinds={"codex", "pi", "claude"})
        self.assertEqual(errors, [])

    def test_rule5_alias_is_resolved_before_checking(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt = "x"
kind = "cd"
""", known_kinds={"codex", "pi", "claude"})
        self.assertEqual(errors, [])

    # rule 6 — retries / rebuilds must not exceed the safe limits
    def test_rule6_retries_over_limit_is_rejected(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt = "x"
retries = 3
""")
        self.assertTrue(any("retries" in e and "2" in e for e in errors), errors)

    def test_rule6_rebuilds_over_limit_is_rejected(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt = "x"
rebuilds = 2
""")
        self.assertTrue(any("rebuilds" in e for e in errors), errors)

    def test_rule6_over_limit_allowed_with_flag(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt = "x"
retries = 5
rebuilds = 3
""", allow_unsafe_limits=True)
        self.assertEqual(errors, [])

    def test_rule6_defaults_are_the_documented_numbers(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt = "x"
""")
        self.assertEqual(errors, [])
        _, flow, _ = hsuper.load_flow(write_flow(self.folder, """
[flow.sample.steps.dev]
prompt = "x"
"""))
        settings = hsuper.step_settings(flow, "dev", {})
        self.assertEqual(settings["retries"], 2)
        self.assertEqual(settings["rebuilds"], 1)

    # rule 7 — step id shape
    def test_rule7_bad_step_id_is_rejected(self):
        errors = self.validate("""
[flow.sample.steps."Bad Id"]
prompt = "x"
""")
        self.assertTrue(any("步骤 ID 不合法" in e for e in errors), errors)

    def test_rule7_step_id_length_limit(self):
        errors = self.validate("""
[flow.sample.steps.%s]
prompt = "x"
""" % ("a" * 33))
        self.assertTrue(any("步骤 ID 不合法" in e for e in errors), errors)

    def test_rule7_valid_step_ids_are_accepted(self):
        errors = self.validate("""
[flow.sample.steps.dev2]
prompt = "x"

[flow.sample.steps.review_2-final]
prompt = "x"
next = ["dev2"]
""")
        self.assertEqual(errors, [])

    # rule 8 — only the documented placeholders
    def test_rule8_unknown_placeholder_is_rejected(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt = "do {{taskk}}"
""")
        self.assertTrue(any("taskk" in e for e in errors), errors)

    def test_rule8_documented_placeholders_are_accepted(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt = "{{task}} {{run_id}} {{step}} {{marker}} {{run_dir}}"
""")
        self.assertEqual(errors, [])

    def test_rule8_near_miss_placeholder_is_rejected(self):
        errors = self.validate("""
[flow.sample.steps.dev]
prompt = "{{task }} {{RUN_ID}}"
""")
        self.assertTrue(errors)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def plan(self, text):
        path = write_flow(self.folder, text)
        _, flow, _ = hsuper.load_flow(path)
        return hsuper.build_plan(flow, "sample")

    def test_topological_order_puts_dependencies_first(self):
        order, roots, _ = self.plan("""
[flow.sample.steps.dev]
prompt = "x"
next = ["review"]

[flow.sample.steps.review]
prompt = "x"
next = ["commit"]

[flow.sample.steps.commit]
prompt = "x"
""")
        self.assertEqual(order, ["dev", "review", "commit"])
        self.assertEqual(roots, ["dev"])

    def test_roots_are_steps_without_incoming_edges(self):
        order, roots, _ = self.plan("""
[flow.sample.steps.a]
prompt = "x"
next = ["c"]

[flow.sample.steps.b]
prompt = "x"
next = ["c"]

[flow.sample.steps.c]
prompt = "x"
""")
        self.assertEqual(sorted(roots), ["a", "b"])
        self.assertEqual(order[-1], "c")

    def test_explicit_start_overrides_roots(self):
        order, roots, _ = self.plan("""
[flow.sample]
start = ["b"]

[flow.sample.steps.a]
prompt = "x"
next = ["b"]

[flow.sample.steps.b]
prompt = "x"
""")
        self.assertEqual(roots, ["b"])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def settings(self, text, step_id="dev", defaults=None):
        path = write_flow(self.folder, text)
        _, flow, _ = hsuper.load_flow(path)
        flow_defaults = flow.get("defaults") or {}
        if defaults is not None:
            flow_defaults = defaults
        return hsuper.step_settings(flow, step_id, flow_defaults)

    def test_defaults_are_inherited(self):
        settings = self.settings("""
[flow.sample.defaults]
kind = "pi"
model = "m1"
args = ["--x"]

[flow.sample.steps.dev]
prompt = "x"
""")
        self.assertEqual(settings["kind"], "pi")
        self.assertEqual(settings["model"], "m1")
        self.assertEqual(settings["args"], ["--x"])

    def test_step_overrides_defaults(self):
        settings = self.settings("""
[flow.sample.defaults]
kind = "pi"
model = "m1"
args = ["--x"]

[flow.sample.steps.dev]
prompt = "x"
kind = "claude"
model = "m2"
args = ["--y"]
""")
        self.assertEqual(settings["kind"], "claude")
        self.assertEqual(settings["model"], "m2")
        self.assertEqual(settings["args"], ["--y"])

    def test_gate_defaults_to_artifact_when_expect_present(self):
        settings = self.settings("""
[flow.sample.steps.dev]
prompt = "x"
expect = ["out.txt"]
""")
        self.assertEqual(settings["gate"], "artifact")

    def test_gate_defaults_to_none_without_expect(self):
        settings = self.settings("""
[flow.sample.steps.dev]
prompt = "x"
""")
        self.assertEqual(settings["gate"], "none")

    def test_kind_alias_is_resolved(self):
        settings = self.settings("""
[flow.sample.steps.dev]
prompt = "x"
kind = "op"
""")
        self.assertEqual(settings["kind"], "opencode")

    def test_role_defaults_to_step_id(self):
        settings = self.settings("""
[flow.sample.steps.dev]
prompt = "x"
""")
        self.assertEqual(settings["role"], "dev")


class DryRunTests(unittest.TestCase):
    """T1 acceptance: --dry-run prints a plan and creates nothing."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_dry_run_prints_plan_and_creates_nothing(self):
        path = write_flow(self.folder, VALID)
        entry = SCRIPTS / "hsuper.py"
        before = sorted(os.listdir(self.folder))
        result = subprocess.run([sys.executable, str(entry),
                                 "--flow-file", str(path),
                                 "--task", "x", "--dry-run"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dev", result.stdout)
        self.assertIn("review", result.stdout)
        self.assertIn("未创建任何资源", result.stdout)
        after = sorted(os.listdir(self.folder))
        self.assertEqual(before, after)
        # No run directory either.
        self.assertFalse((self.folder / ".herdr-recipes").exists())

    def test_dry_run_reports_validation_errors(self):
        path = write_flow(self.folder, """
[flow.sample.steps.dev]
prompt = "x"
next = ["ghost"]
""")
        entry = SCRIPTS / "hsuper.py"
        result = subprocess.run([sys.executable, str(entry),
                                 "--flow-file", str(path),
                                 "--task", "x", "--dry-run"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ghost", result.stderr)

    def test_dry_run_requires_a_flow(self):
        entry = SCRIPTS / "hsuper.py"
        result = subprocess.run([sys.executable, str(entry), "--dry-run"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
