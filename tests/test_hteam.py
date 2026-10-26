import json
import os
from pathlib import Path
import re
import subprocess
import unittest

from test_hopen_dispatch import FakeHerdrLauncherFixture, REPO_ROOT


TEAM = REPO_ROOT / "scripts" / "hteam.sh"
CONTEXT = REPO_ROOT / "scripts" / "hteam-context.sh"


class TeamLauncherTests(FakeHerdrLauncherFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.environment["HTEAM_CONFIG"] = ""

    def launched_context(self):
        prompt = self.calls_for(self.read_calls(), "agent", "prompt")[0][3]
        encoded = prompt.split("## Runtime context (authoritative scope)\n", 1)[1]
        context, request = encoded.split("## User's first request\n", 1)
        return json.loads(context), request

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
        context = json.loads(context)
        expected = {
            "workspace_id": "w-test", "tab_id": "w-test:t1", "coordinator_pane_id": "w-test:p1",
            "coordinator_name": starts[0][2], "cwd": str(REPO_ROOT),
            "launcher_core": str(REPO_ROOT / "scripts" / "hopen.sh"),
            "status_entry": str(REPO_ROOT / "scripts" / "hstatus.sh"),
            "context_entry": str(CONTEXT), "coordinator_kind": "codex",
            "team_config": {"worker_kind": "codex", "max_workers": 4, "skills": {}}, "config_path": None,
        }
        self.assertEqual({key: context[key] for key in expected}, expected)
        self.assertRegex(context["team_id"], r"^team-[0-9T]+-[0-9]+-[0-9]+$")
        self.assertEqual(context["record_dir"], str(REPO_ROOT / ".herdr-recipes" / "teams" / context["team_id"]))
        self.assertFalse(Path(context["record_dir"]).exists(), "launcher must not create a task record or queue")
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
        context, _ = self.launched_context()
        self.assertEqual(context["coordinator_kind"], "claude")
        self.assertEqual(context["team_config"]["worker_kind"], "claude")

    def test_project_config_explicit_config_and_cli_override_precedence(self):
        project = Path(self.temp_dir.name) / "team project"
        project.mkdir()
        project_config = project / ".herdr-team.json"
        project_config.write_text(json.dumps({"worker_kind": "pi", "max_workers": 2}))
        explicit = project / "custom.json"
        explicit.write_text(json.dumps({"worker_kind": "cc", "max_workers": 3}))
        cases = [
            ([], {}, {"worker_kind": "pi", "max_workers": 2, "skills": {}}, project_config),
            (["--config", str(explicit)], {}, {"worker_kind": "claude", "max_workers": 3, "skills": {}}, explicit),
            ([], {"HTEAM_CONFIG": str(explicit)}, {"worker_kind": "claude", "max_workers": 3, "skills": {}}, explicit),
            (["--config", str(explicit), "--worker-kind", "op", "--max-workers", "6"], {},
             {"worker_kind": "opencode", "max_workers": 6, "skills": {}}, explicit),
        ]
        for options, environment, expected, config in cases:
            with self.subTest(options=options, environment=environment):
                self.clear_calls()
                result = self.run_launcher(TEAM, "-C", str(project), *options, **environment)
                self.assertEqual(result.returncode, 0, result.stderr)
                context, _ = self.launched_context()
                self.assertEqual(context["team_config"], expected)
                self.assertEqual(context["config_path"], str(config.resolve()))
                self.assertEqual(len(self.calls_for(self.read_calls(), "agent", "start")), 1)
        self.assertEqual(json.loads(project_config.read_text()), {"worker_kind": "pi", "max_workers": 2})

    def test_explicit_request_is_not_parsed_or_used_to_change_launch_config(self):
        request = '这次用六个 pi 调研，汇总成报告；不要解析我举的 research 示例。'
        result = self.run_launcher(TEAM, "--max-workers", "2", "-p", request)
        self.assertEqual(result.returncode, 0, result.stderr)
        context, actual_request = self.launched_context()
        self.assertEqual(context["team_config"]["max_workers"], 2)
        self.assertEqual(actual_request, request)
        self.assertEqual(len(self.calls_for(self.read_calls(), "agent", "start")), 1)

    def test_skill_conventions_pass_through_and_reject_unknown_roles(self):
        project = Path(self.temp_dir.name) / "skills project"
        project.mkdir()
        config = project / ".herdr-team.json"
        skills = {"implement": "implement", "review": "code-review",
                  "diagnose": "diagnosing-bugs", "route": "ask-matt"}
        config.write_text(json.dumps({"skills": skills}))
        result = self.run_launcher(TEAM, "-C", str(project))
        self.assertEqual(result.returncode, 0, result.stderr)
        context, _ = self.launched_context()
        self.assertEqual(context["team_config"]["skills"], skills)
        self.assertEqual(json.loads(config.read_text()), {"skills": skills})

        for body in ('{"skills":[]}', '{"skills":"implement"}',
                     '{"skills":{"review":""}}', '{"skills":{"review":"Bad Name"}}',
                     '{"skills":{"verify":"code-review"}}', '{"skills":{"review":null}}'):
            with self.subTest(body=body):
                config.write_text(body)
                self.clear_calls()
                result = self.run_launcher(TEAM, "--config", str(config))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.read_calls(), [])

    def test_project_config_is_found_by_walking_up_to_the_repo_root(self):
        project = Path(self.temp_dir.name) / "walk project"
        nested = project / "pkg" / "src"
        nested.mkdir(parents=True)
        (project / ".git").mkdir()
        project_config = project / ".herdr-team.json"
        project_config.write_text(json.dumps({"max_workers": 2,
                                              "skills": {"review": "code-review"}}))
        for target in (project, project / "pkg", nested):
            with self.subTest(target=target.name):
                self.clear_calls()
                result = self.run_launcher(TEAM, "-C", str(target))
                self.assertEqual(result.returncode, 0, result.stderr)
                context, _ = self.launched_context()
                self.assertEqual(context["team_config"]["max_workers"], 2)
                self.assertEqual(context["team_config"]["skills"], {"review": "code-review"})
                self.assertEqual(context["config_path"], str(project_config.resolve()))

    def test_linked_worktree_inherits_the_main_worktrees_project_config(self):
        project = Path(self.temp_dir.name) / "main project"
        project.mkdir()
        (project / "README.md").write_text("base\n")
        project_config = project / ".herdr-team.json"
        git_env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        for command in (["init", "-q"], ["add", "README.md"], ["commit", "-qm", "base"]):
            run = subprocess.run(["git", "-C", str(project), *command], capture_output=True,
                                 text=True, env={**os.environ, **git_env})
            self.assertEqual(run.returncode, 0, run.stderr)
        worktree = Path(self.temp_dir.name) / "linked worktree"
        run = subprocess.run(["git", "-C", str(project), "worktree", "add", "-q", str(worktree), "-b", "wt"],
                             capture_output=True, text=True, env={**os.environ, **git_env})
        self.assertEqual(run.returncode, 0, run.stderr)
        # Config lives only in the main checkout as a local, uncommitted file.
        project_config.write_text(json.dumps({"skills": {"implement": "implement"}}))
        self.assertFalse((worktree / ".herdr-team.json").exists())
        self.assertTrue((worktree / ".git").is_file(), "linked worktree root carries a .git file")
        self.clear_calls()
        result = self.run_launcher(TEAM, "-C", str(worktree))
        self.assertEqual(result.returncode, 0, result.stderr)
        context, _ = self.launched_context()
        self.assertEqual(context["team_config"]["skills"], {"implement": "implement"})
        self.assertEqual(context["config_path"], str(project_config.resolve()))

    def test_config_lookup_stops_at_a_repo_root_without_config(self):
        outer = Path(self.temp_dir.name) / "outer"
        inner = outer / "inner"
        inner.mkdir(parents=True)
        (inner / ".git").mkdir()
        (outer / ".herdr-team.json").write_text(json.dumps({"max_workers": 3}))
        # A chosen target directory never inherits config from outside its repo root.
        nested = inner / "pkg"
        nested.mkdir()
        self.clear_calls()
        result = self.run_launcher(TEAM, "-C", str(nested))
        self.assertEqual(result.returncode, 0, result.stderr)
        context, _ = self.launched_context()
        self.assertEqual(context["team_config"]["max_workers"], 4)
        self.assertIsNone(context["config_path"])

    def test_invalid_policy_fails_before_any_herdr_operation(self):
        config = Path(self.temp_dir.name) / "bad.json"
        for body in ('[]', '{} {}', '{', '{"max_workers":0}', '{"max_workers":1.5}',
                     '{"max_workers":true}', '{"max_workers":null}',
                     '{"worker_kind":""}', '{"worker_kind":[]}', '{"max_worker":4}'):
            with self.subTest(body=body):
                config.write_text(body)
                self.clear_calls()
                result = self.run_launcher(TEAM, "--config", str(config))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(self.read_calls(), [])
        for options in (("--max-workers", "-1"), ("--max-workers", "2.5"), ("--worker-kind", "bad kind"),
                        ("--config", "/no/such/team-config.json"), ("--max-workers", "")):
            with self.subTest(options=options):
                result = self.run_launcher(TEAM, *options)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.read_calls(), [])

    def test_in_place_builder_uses_same_policy_without_creating_resources_or_records(self):
        project = Path(self.temp_dir.name) / "in-place project"
        project.mkdir()
        (project / ".herdr-team.json").write_text('{"worker_kind":null,"max_workers":3}')
        launch = self.run_launcher(TEAM, "-C", str(project), "-k", "cc")
        self.assertEqual(launch.returncode, 0, launch.stderr)
        launched, _ = self.launched_context()
        self.clear_calls()
        contexts = []
        for _ in range(2):
            result = self.run_launcher(CONTEXT, "-C", str(project), "--workspace", "actual-ws",
                                       "--tab", "actual-tab", "--pane", "actual-pane", "--kind", "cc")
            self.assertEqual(result.returncode, 0, result.stderr)
            context = json.loads(result.stdout)
            self.assertEqual(context["team_config"], launched["team_config"])
            self.assertEqual(context["coordinator_kind"], "claude")
            self.assertIsNone(context["coordinator_name"])
            self.assertEqual(context["coordinator_pane_id"], "actual-pane")
            contexts.append(context)
        self.assertNotEqual(contexts[0]["team_id"], contexts[1]["team_id"])
        self.assertEqual(self.read_calls(), [])
        self.assertFalse((project / ".herdr-recipes").exists())

    def test_shared_context_reference_documents_exist(self):
        # Injected prompts are read from another cwd; disclosed instructions must be reachable.
        prompt = (REPO_ROOT / "scripts" / "coordinator-prompt.md").read_text()
        for target in re.findall(r"\]\(([^)]+)\)", prompt):
            self.assertTrue((TEAM.parent / target).is_file(), f"unreachable instruction: {target}")

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
        for flag in ("--help", "--version"):
            result = self.run_launcher(CONTEXT, flag)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_launcher(CONTEXT, "--kind", "pi")
        self.assertEqual(result.returncode, 2)
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
