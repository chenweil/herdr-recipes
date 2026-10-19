import json
from pathlib import Path
import unittest

from test_hopen_dispatch import FakeHerdrLauncherFixture, REPO_ROOT


TEAM = REPO_ROOT / "scripts" / "hteam.sh"


class TeamLauncherTests(FakeHerdrLauncherFixture, unittest.TestCase):
    def test_default_bootstraps_only_a_coordinator_and_injects_exact_scope(self):
        result = self.run_launcher(TEAM)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.read_calls()
        starts = self.calls_for(calls, "agent", "start")
        prompts = self.calls_for(calls, "agent", "prompt")
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0][2], "hopen-w-test-coordinator")
        self.assertEqual(starts[0][starts[0].index("--kind") + 1], "codex")
        self.assertEqual(starts[0][starts[0].index("--pane") + 1], "w-test:p1")
        self.assertEqual(len(prompts), 1)
        prompt = prompts[0][3]
        encoded = prompt.split("## Runtime context (authoritative scope)\n", 1)[1]
        context, request = encoded.split("## User's first request\n", 1)
        self.assertEqual(json.loads(context), {
            "workspace_id": "w-test", "tab_id": "w-test:t1", "coordinator_pane_id": "w-test:p1",
            "coordinator_name": starts[0][2], "cwd": str(REPO_ROOT),
            "launcher_core": str(REPO_ROOT / "scripts" / "hopen.sh"),
            "status_entry": str(REPO_ROOT / "scripts" / "hstatus.sh"),
        })
        self.assertIn("不要自行创建 worker", request)
        self.assertIn("同一", prompt)
        self.assertIn("待命", prompt)
        self.assertIn("不是某个任务的完成屏障", prompt)
        self.assertIn("--source visible", prompt)
        self.assertEqual(self.calls_for(calls, "pane", "split"), [])
        self.assertEqual(self.calls_for(calls, "workspace", "focus"), [["workspace", "focus", "w-test"]])
        self.assertEqual(result.stdout, "ws=w-test panes=w-test:p1 coordinator=hopen-w-test-coordinator\n")
        notifications = self.calls_for(calls, "notification", "show")
        self.assertEqual(len(notifications), 1)
        self.assertIn("done", notifications[0])

    def test_initial_request_alias_path_and_no_focus(self):
        project = Path(self.temp_dir.name) / "project with spaces"
        project.mkdir()
        request = '先准备两个 codex，暂时不要工作；随后我再分配。"$(false)"'
        result = self.run_launcher(TEAM, "-C", str(project), "-k", "cc", "-p", request, "--no-focus")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.read_calls()
        create = self.calls_for(calls, "workspace", "create")[0]
        self.assertEqual(create[create.index("--cwd") + 1], str(project.resolve()))
        self.assertIn("--no-focus", create)
        start = self.calls_for(calls, "agent", "start")[0]
        self.assertEqual(start[start.index("--kind") + 1], "claude")
        prompt = self.calls_for(calls, "agent", "prompt")[0][3]
        self.assertTrue(prompt.endswith(request))
        self.assertEqual(self.calls_for(calls, "pane", "split"), [])
        self.assertEqual(self.calls_for(calls, "workspace", "focus"), [])

    def test_start_and_prompt_failure_are_visible_and_retain_the_workspace(self):
        for failure in ({"FAKE_FAIL_START_KINDS": "codex"},
                        {"FAKE_FAIL_PROMPT_CONTAINS": "尚无工作任务"}):
            with self.subTest(failure=failure):
                self.clear_calls()
                result = self.run_launcher(TEAM, **failure)
                self.assertEqual(result.returncode, 1)
                self.assertIn("setup incomplete", result.stderr)
                self.assertIn("workspace=w-test", result.stderr)
                calls = self.read_calls()
                self.assertEqual(self.calls_for(calls, "workspace", "close"), [])
                self.assertEqual(self.calls_for(calls, "pane", "split"), [])
                self.assertEqual(json.loads(self.lifecycle_path.read_text()),
                                 {"workspace_exists": True, "panes": ["w-test:p1"]})
                notification = self.calls_for(calls, "notification", "show")[0]
                self.assertIn("request", notification)
                self.assertNotIn("done", notification)

    def test_workspace_create_failure_never_attempts_agent_start(self):
        result = self.run_launcher(TEAM, FAKE_FAIL_WORKSPACE_CREATE="1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("workspace_create_failed", result.stderr)
        self.assertEqual(self.calls_for(self.read_calls(), "agent", "start"), [])

    def test_help_version_and_invalid_arguments_do_not_call_herdr(self):
        for flag in ("--help", "-v", "-V", "--version"):
            result = self.run_launcher(TEAM, flag)
            self.assertEqual(result.returncode, 0, result.stderr)
            if flag != "--help":
                self.assertEqual(result.stdout, f"hteam {(REPO_ROOT / 'VERSION').read_text().strip()}\n")
            else:
                self.assertIn("no background queue", result.stdout)
        for args in (("--prompt",), ("--path", "/no/such/team/path"), ("--kind", ""), ("--bad",)):
            with self.subTest(args=args):
                result = self.run_launcher(TEAM, *args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
        self.assertEqual(self.read_calls(), [])

    def test_names_remain_unique_case_safe_and_within_herdr_limit(self):
        names = []
        for workspace in ("wAa", "waa", "w-" + "long" * 20):
            self.clear_calls()
            result = self.run_launcher(TEAM, FAKE_WORKSPACE_ID=workspace)
            self.assertEqual(result.returncode, 0, result.stderr)
            name = self.calls_for(self.read_calls(), "agent", "start")[0][2]
            self.assertRegex(name, r"^[a-z][a-z0-9_-]{0,31}$")
            names.append(name)
        self.assertEqual(len(set(names)), 3)


if __name__ == "__main__":
    unittest.main()
