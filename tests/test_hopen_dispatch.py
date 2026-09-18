import json
import os
import pathlib
import re
import subprocess
import tempfile
import textwrap
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
HOPEN_ONCE = REPO_ROOT / "scripts" / "hopen-once.sh"
HOPEN = REPO_ROOT / "scripts" / "hopen.sh"

FAKE_HERDR = textwrap.dedent(
    r"""
    #!/usr/bin/env python3
    import json
    import os
    import pathlib
    import sys


    args = sys.argv[1:]
    log_path = pathlib.Path(os.environ["FAKE_HERDR_LOG"])
    with log_path.open("a") as log:
        log.write(json.dumps(args) + "\n")


    def respond(payload, status=0):
        print(json.dumps(payload))
        raise SystemExit(status)


    def option(name):
        index = args.index(name)
        return args[index + 1]


    if args[:2] == ["workspace", "list"]:
        respond({"result": {"workspaces": []}})
    if args[:2] == ["tab", "list"]:
        respond({"result": {"tabs": []}})
    if args[:2] == ["workspace", "create"]:
        workspace = os.environ.get("FAKE_WORKSPACE_ID", "w-test")
        respond(
            {
                "result": {
                    "workspace": {"workspace_id": workspace},
                    "root_pane": {"pane_id": workspace + ":p1"},
                    "tab": {"tab_id": workspace + ":t1"},
                }
            }
        )
    if args[:2] == ["pane", "split"]:
        state_path = pathlib.Path(os.environ["FAKE_HERDR_STATE"])
        count = int(state_path.read_text() or "0") if state_path.exists() else 0
        state_path.write_text(str(count + 1))
        workspace = os.environ.get("FAKE_WORKSPACE_ID", "w-test")
        respond({"result": {"pane": {"pane_id": "%s:p%d" % (workspace, count + 2)}}})
    if args[:2] in (
        ["tab", "rename"],
        ["pane", "rename"],
        ["workspace", "focus"],
        ["workspace", "close"],
    ):
        respond({"result": {"type": "ok"}})
    if args[:2] == ["agent", "start"]:
        kind = option("--kind")
        failed_kinds = os.environ.get("FAKE_FAIL_START_KINDS", "").split(",")
        if kind in failed_kinds:
            respond(
                {
                    "error": {
                        "code": "unsupported_kind",
                        "message": "fake diagnostic for " + kind,
                    }
                },
                1,
            )
        respond({"result": {"type": "agent_started"}})
    if args[:2] == ["agent", "prompt"]:
        name = args[2]
        prompt = args[3]
        failure_marker = os.environ.get("FAKE_FAIL_PROMPT_CONTAINS", "")
        if failure_marker and (failure_marker in name or failure_marker in prompt):
            respond(
                {
                    "error": {
                        "code": "prompt_rejected",
                        "message": "fake prompt diagnostic",
                    }
                },
                1,
            )
        respond({"result": {"type": "prompt_submitted"}})
    if args[:2] == ["notification", "show"]:
        respond({"result": {"type": "notification_show", "shown": True}})

    respond({"error": {"code": "unknown_fake_command", "message": " ".join(args)}}, 1)
    """
).lstrip()


class FakeHerdrLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp_dir.name)
        self.bin_dir = root / "bin"
        self.bin_dir.mkdir()
        fake_herdr = self.bin_dir / "herdr"
        fake_herdr.write_text(FAKE_HERDR)
        fake_herdr.chmod(0o755)
        self.log_path = root / "herdr.log"
        self.state_path = root / "herdr.state"
        self.conf_path = root / "hopen-agents.conf"
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "PATH": str(self.bin_dir) + os.pathsep + self.environment["PATH"],
                "FAKE_HERDR_LOG": str(self.log_path),
                "FAKE_HERDR_STATE": str(self.state_path),
                "FAKE_WORKSPACE_ID": "w-test",
                "FAKE_FAIL_START_KINDS": "",
                "FAKE_FAIL_PROMPT_CONTAINS": "",
                "HOPEN_CONF": str(self.conf_path),
            }
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_launcher(self, script, *args, **overrides):
        environment = self.environment.copy()
        environment.update(overrides)
        return subprocess.run(
            ["bash", str(script), *args],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )

    def read_calls(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def write_conf(self, body):
        self.conf_path.write_text(textwrap.dedent(body).lstrip())

    def clear_calls(self):
        if self.log_path.exists():
            self.log_path.unlink()
        if self.state_path.exists():
            self.state_path.unlink()

    @staticmethod
    def calls_for(calls, *prefix):
        return [call for call in calls if call[: len(prefix)] == list(prefix)]

    def test_canonical_kind_reaches_herdr_without_executable_preflight(self):
        result = self.run_launcher(HOPEN_ONCE, "-l", "11", "cursor", "codex")

        starts = self.calls_for(self.read_calls(), "agent", "start")
        kinds = [start[start.index("--kind") + 1] for start in starts]
        self.assertEqual(result.returncode, 0)
        self.assertEqual(kinds, ["cursor", "codex"])

    def test_aliases_and_full_pi_kind_are_forwarded(self):
        result = self.run_launcher(HOPEN_ONCE, "-l", "11", "op", "pi")

        starts = self.calls_for(self.read_calls(), "agent", "start")
        kinds = [start[start.index("--kind") + 1] for start in starts]
        self.assertEqual(result.returncode, 0)
        self.assertEqual(kinds, ["opencode", "pi"])

    def test_layout_221_builds_five_panes_and_dispatches_in_visual_order(self):
        result = self.run_launcher(
            HOPEN_ONCE,
            "-l",
            "221",
            "codex",
            "pi",
            "claude",
            "hermes",
            "opencode",
            FAKE_WORKSPACE_ID="w-221",
        )

        calls = self.read_calls()
        splits = self.calls_for(calls, "pane", "split")
        starts = self.calls_for(calls, "agent", "start")
        split_targets = [
            (call[2], call[call.index("--direction") + 1]) for call in splits
        ]
        target_panes = [call[call.index("--pane") + 1] for call in starts]
        names = [call[2] for call in starts]

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            split_targets,
            [
                ("w-221:p1", "right"),
                ("w-221:p2", "right"),
                ("w-221:p1", "down"),
                ("w-221:p2", "down"),
            ],
        )
        self.assertEqual(
            target_panes,
            ["w-221:p1", "w-221:p2", "w-221:p3", "w-221:p4", "w-221:p5"],
        )
        self.assertEqual(
            names,
            [
                "hopen-w-221-left-top",
                "hopen-w-221-middle-top",
                "hopen-w-221-right",
                "hopen-w-221-left-bottom",
                "hopen-w-221-middle-bottom",
            ],
        )

    def test_layout_122_builds_five_panes_and_maps_middle_and_right_columns(self):
        result = self.run_launcher(
            HOPEN_ONCE,
            "-l",
            "122",
            "codex",
            "pi",
            "claude",
            "hermes",
            "opencode",
            FAKE_WORKSPACE_ID="w-122",
        )

        calls = self.read_calls()
        splits = self.calls_for(calls, "pane", "split")
        starts = self.calls_for(calls, "agent", "start")
        split_targets = [
            (call[2], call[call.index("--direction") + 1]) for call in splits
        ]
        target_panes = [call[call.index("--pane") + 1] for call in starts]
        names = [call[2] for call in starts]

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            split_targets,
            [
                ("w-122:p1", "right"),
                ("w-122:p2", "right"),
                ("w-122:p3", "down"),
                ("w-122:p2", "down"),
            ],
        )
        self.assertEqual(
            target_panes,
            ["w-122:p1", "w-122:p2", "w-122:p3", "w-122:p5", "w-122:p4"],
        )
        self.assertEqual(
            names,
            [
                "hopen-w-122-left",
                "hopen-w-122-middle-top",
                "hopen-w-122-right-top",
                "hopen-w-122-middle-bottom",
                "hopen-w-122-right-bottom",
            ],
        )

    def test_hopen_conf_dispatch_supports_new_layout_positions(self):
        self.write_conf(
            """
            [layout.221.panes.left-top]
            kind = "codex"
            [layout.221.panes.middle-top]
            kind = "pi"
            [layout.221.panes.right]
            kind = "claude"
            [layout.221.panes.left-bottom]
            kind = "hermes"
            [layout.221.panes.middle-bottom]
            kind = "opencode"
            """
        )
        result = self.run_launcher(HOPEN, "221", FAKE_WORKSPACE_ID="w-conf-221")

        starts = self.calls_for(self.read_calls(), "agent", "start")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            [call[call.index("--pane") + 1] for call in starts],
            [
                "w-conf-221:p1",
                "w-conf-221:p2",
                "w-conf-221:p3",
                "w-conf-221:p4",
                "w-conf-221:p5",
            ],
        )

    def test_reopening_layout_uses_unique_readable_names(self):
        first = self.run_launcher(
            HOPEN_ONCE,
            "-l",
            "11",
            "codex",
            "pi",
            FAKE_WORKSPACE_ID="w-first",
        )
        first_names = [call[2] for call in self.calls_for(self.read_calls(), "agent", "start")]
        self.clear_calls()
        second = self.run_launcher(
            HOPEN_ONCE,
            "-l",
            "11",
            "codex",
            "pi",
            FAKE_WORKSPACE_ID="w-second",
        )
        second_names = [call[2] for call in self.calls_for(self.read_calls(), "agent", "start")]
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertTrue(first_names)
        self.assertEqual(len(second_names), 2)
        self.assertTrue(set(first_names).isdisjoint(second_names))

        self.clear_calls()
        long_workspace = "w" + "x" * 90
        self.run_launcher(
            HOPEN_ONCE,
            "-l",
            "11",
            "codex",
            "pi",
            FAKE_WORKSPACE_ID=long_workspace,
        )
        long_names = [call[2] for call in self.calls_for(self.read_calls(), "agent", "start")]
        all_names = first_names + second_names + long_names
        self.assertEqual(len(first_names), 2)
        self.assertEqual(len(set(first_names)), 2)
        self.assertTrue(all(re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name) for name in all_names))
        self.assertTrue(all(len(name) == 32 for name in long_names))
        self.assertTrue(all(re.search(r"-[0-9]{1,6}$", name) for name in long_names))

    def test_case_distinct_workspace_ids_do_not_collide_after_sanitizing(self):
        self.run_launcher(
            HOPEN_ONCE,
            "-l",
            "11",
            "codex",
            "pi",
            FAKE_WORKSPACE_ID="wA",
        )
        first_names = [call[2] for call in self.calls_for(self.read_calls(), "agent", "start")]
        self.clear_calls()
        self.run_launcher(
            HOPEN_ONCE,
            "-l",
            "11",
            "codex",
            "pi",
            FAKE_WORKSPACE_ID="wa",
        )
        second_names = [call[2] for call in self.calls_for(self.read_calls(), "agent", "start")]

        self.assertTrue(set(first_names).isdisjoint(second_names))

    def test_success_dispatch_emits_one_ready_notification_and_one_stdout_line(self):
        result = self.run_launcher(
            HOPEN_ONCE,
            "-l",
            "11",
            "-k",
            "codex",
            "-p",
            "first prompt",
            "-k",
            "pi",
            "-p",
            "second prompt",
            FAKE_WORKSPACE_ID="w-ready",
        )

        calls = self.read_calls()
        notifications = self.calls_for(calls, "notification", "show")
        prompts = self.calls_for(calls, "agent", "prompt")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ["ws=w-ready panes=w-ready:p1 w-ready:p2"])
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0][2], "Recipe ready")
        self.assertEqual(notifications[0][notifications[0].index("--sound") + 1], "done")
        self.assertEqual(len(prompts), 2)
        self.assertTrue(all("--wait" not in prompt for prompt in prompts))
        self.assertEqual(result.stderr.count("[hopen]"), 2)

    def test_dispatch_failures_are_accumulated_without_aborting_later_panes(self):
        self.write_conf(
            """
            [layout.22.panes.left-top]
            kind = "not-a-kind"
            prompt = "first"

            [layout.22.panes.left-bottom]
            kind = "pi"
            prompt = "prompt-fail"

            [layout.22.panes.right-top]
            kind = "codex"
            prompt = "third"

            [layout.22.panes.right-bottom]
            kind = "claude"
            prompt = "fourth"
            """
        )
        result = self.run_launcher(
            HOPEN,
            "22",
            FAKE_WORKSPACE_ID="w-failure",
            FAKE_FAIL_START_KINDS="not-a-kind",
            FAKE_FAIL_PROMPT_CONTAINS="prompt-fail",
        )

        calls = self.read_calls()
        starts = self.calls_for(calls, "agent", "start")
        prompts = self.calls_for(calls, "agent", "prompt")
        notifications = self.calls_for(calls, "notification", "show")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(starts), 4)
        self.assertEqual(len(prompts), 3)
        self.assertEqual(result.stdout.splitlines(), ["ws=w-failure panes=w-failure:p1 w-failure:p2 w-failure:p3 w-failure:p4"])
        self.assertIn("fake diagnostic for not-a-kind", result.stderr)
        self.assertIn("fake prompt diagnostic", result.stderr)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0][2], "Recipe dispatch failed")
        body = notifications[0][notifications[0].index("--body") + 1]
        self.assertIn("Dispatch failures", body)
        self.assertIn("agent start failed", body)
        self.assertIn("initial prompt failed", body)
        self.assertEqual(notifications[0][notifications[0].index("--sound") + 1], "request")


if __name__ == "__main__":
    unittest.main()
