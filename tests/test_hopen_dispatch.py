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
HOPEN_SH = REPO_ROOT / "scripts" / "hopen.sh"
HOPEN_ONCE_SH = HOPEN_ONCE
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

    def assert_five_pane_geometry(self, calls, code):
        """Replay actual split arguments; the single pane and 2x2 grid each own half.

        This models CLI geometry independently of the stub, which only returns IDs.
        Live Herdr smoke checks validate the model's rounding separately.
        """
        splits = self.calls_for(calls, "pane", "split")
        workspace = splits[0][2].rsplit(":", 1)[0]
        positions = {
            "221": [(0, 0, 1, 1), (1, 0, 1, 1), (2, 0, 2, 2),
                    (0, 1, 1, 1), (1, 1, 1, 1)],
            "122": [(0, 0, 2, 2), (2, 0, 1, 1), (3, 0, 1, 1),
                    (3, 1, 1, 1), (2, 1, 1, 1)],
        }
        for width, height in ((120, 40), (124, 44), (123, 41)):
            with self.subTest(layout=code, size=(width, height)):
                rectangles = {f"{workspace}:p1": (0, 0, width, height)}
                for index, call in enumerate(splits, start=2):
                    parent = call[2]
                    x, y, w, h = rectangles[parent]
                    ratio = float(call[call.index("--ratio") + 1]) if "--ratio" in call else 0.5
                    direction = call[call.index("--direction") + 1]
                    if direction == "right":
                        retained = int(w * ratio + 0.5)
                        rectangles[parent] = (x, y, retained, h)
                        new = (x + retained, y, w - retained, h)
                    else:
                        self.assertEqual(direction, "down")
                        retained = int(h * ratio + 0.5)
                        rectangles[parent] = (x, y, w, retained)
                        new = (x, y + retained, w, h - retained)
                    rectangles[f"{workspace}:p{index}"] = new
                tolerance = 0 if width % 4 == 0 and height % 2 == 0 else 1
                for index, (x, y, w, h) in enumerate(positions[code], start=1):
                    expected = (x * width / 4, y * height / 2,
                                w * width / 4, h * height / 2)
                    actual = rectangles[f"{workspace}:p{index}"]
                    for found, wanted in zip(actual, expected):
                        self.assertAlmostEqual(found, wanted, delta=tolerance,
                                               msg=f"{code}: p{index}: {actual} != {expected}")

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
        self.assert_five_pane_geometry(calls, "221")
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
        self.assert_five_pane_geometry(calls, "122")
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

    # --- issue #9: 21 / 31 must place kinds in (y, x) visual order, not
    # _h_row_major's old left-column-first order. The numbered switcher
    # (prefix+N) and the smoke matrix already agree on this order; this
    # test pins the script dispatcher to the same contract.
    #
    # 21 layout split order (from hopen.sh):
    #   p1 = left-top (root), p2 = right (split right from p1),
    #   p3 = left-bottom (split down from p1).
    # Visual (y, x) order: left-top (p1) → right (p2) → left-bottom (p3).
    def test_layout_21_dispatches_in_visual_order_not_creation_order(self):
        result = self.run_launcher(
            HOPEN_ONCE,
            "-l",
            "21",
            "codex",
            "pi",
            "claude",
            FAKE_WORKSPACE_ID="w-21v",
        )

        calls = self.read_calls()
        starts = self.calls_for(calls, "agent", "start")
        target_panes = [call[call.index("--pane") + 1] for call in starts]
        names = [call[2] for call in starts]

        self.assertEqual(result.returncode, 0)
        # kind 1 (codex) → visual 1 (left-top) → pane p1
        # kind 2 (pi)    → visual 2 (right)    → pane p2
        # kind 3 (claude)→ visual 3 (left-bottom)→ pane p3
        self.assertEqual(
            target_panes,
            ["w-21v:p1", "w-21v:p2", "w-21v:p3"],
        )
        self.assertEqual(
            names,
            [
                "hopen-w-21v-left-top",
                "hopen-w-21v-right",
                "hopen-w-21v-left-bottom",
            ],
        )

    # 31 layout split order (from hopen.sh):
    #   p1 = left-top (root), p2 = right (split right from p1),
    #   p3 = left-mid (split down from p1), p4 = left-bottom (split down from p3).
    # Visual (y, x) order: left-top (p1) → right (p2) → left-mid (p3) → left-bottom (p4).
    def test_layout_31_dispatches_in_visual_order_not_creation_order(self):
        result = self.run_launcher(
            HOPEN_ONCE,
            "-l",
            "31",
            "codex",
            "pi",
            "claude",
            "hermes",
            FAKE_WORKSPACE_ID="w-31v",
        )

        calls = self.read_calls()
        starts = self.calls_for(calls, "agent", "start")
        target_panes = [call[call.index("--pane") + 1] for call in starts]
        names = [call[2] for call in starts]

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            target_panes,
            ["w-31v:p1", "w-31v:p2", "w-31v:p3", "w-31v:p4"],
        )
        self.assertEqual(
            names,
            [
                "hopen-w-31v-left-top",
                "hopen-w-31v-right",
                "hopen-w-31v-left-mid",
                "hopen-w-31v-left-bottom",
            ],
        )

    # 21 layout with 3 kinds + 3 overflow names. The positional args after
    # the kinds are prepended to NAMES and mapped to visual positions.
    # A→visual 1 (left-top)=p1, B→visual 2 (right)=p2, C→visual 3 (left-bottom)=p3.
    def test_positional_overflow_lands_names_on_visual_positions_for_21(self):
        result = self.run_launcher(
            HOPEN_ONCE,
            "-l",
            "21",
            "pi",
            "pi",
            "codex",
            "A",
            "B",
            "C",
            FAKE_WORKSPACE_ID="w-21o",
        )

        calls = self.read_calls()
        starts = self.calls_for(calls, "agent", "start")
        target_panes = [call[call.index("--pane") + 1] for call in starts]
        names = [call[2] for call in starts]
        # `pane rename` carries the literal (pane_id, new_label) pair; this
        # is what proves A/B/C truly landed on visual positions 1/2/3
        # (left-top / right / left-bottom) rather than just changing the
        # agent's agent-name string.
        renames = [(call[2], call[3]) for call in self.calls_for(calls, "pane", "rename")]

        self.assertEqual(result.returncode, 0)
        # A/B/C land on visual positions 1/2/3 → panes p1/p2/p3.
        # The kind binding is unchanged (pi/pi/codex); what changes is which
        # pane each kind's agent occupies.
        self.assertEqual(
            target_panes,
            ["w-21o:p1", "w-21o:p2", "w-21o:p3"],
        )
        self.assertEqual(
            names,
            [
                "hopen-w-21o-left-top",
                "hopen-w-21o-right",
                "hopen-w-21o-left-bottom",
            ],
        )
        self.assertEqual(
            renames,
            [
                ("w-21o:p1", "A"),
                ("w-21o:p2", "B"),
                ("w-21o:p3", "C"),
            ],
        )


class LayoutVisualOrderContractTests(FakeHerdrLauncherTests):
    """契约测试：所有 9 个 layout 的视觉位置映射和派位顺序。

    每个 layout 必须满足下列契约：
      1. 视觉位置总数 = pane 总数
      2. 视觉位置无重复
      3. 创建 idx 0..N-1 与视觉位置一一映射（无遗漏、无重复）
      4. hopen-once.sh 按视觉阅读顺序派位
      5. hopen.sh 用 conf 与 hopen-once.sh 给出相同的 kind → pane 映射

    来源：pane:plan 阶段 0.1；docs/future-directions.md §4.6 测试盲点；
          解决 CHANGELOG 0.5.0 重复映射导致的 21/31 派位 bug
    """

    # (code, pane_count, creation_positions[idx_0→name], visual_positions[(y,x)→name])
    #
    # creation_positions 是按 hopen.sh 的 created idx 顺序（即 _position_for 的输出）
    # 排列的视觉位置名。visual_positions 是按 (y, x) 阅读顺序排列的视觉位置名。
    #
    # 22 与 122 的两者不同：hopen.sh 内部按"先抢右列再下分层"创建，但视觉
    # 阅读时 left-bottom 应该排在 right-bottom 之前（22 同样：middle-bottom
    # 排在 right-bottom 之前，122）。这是 0.5.0 修复的同种问题。
    LAYOUT_CONTRACTS = [
        (
            "11",
            2,
            ["left", "right"],
            ["left", "right"],
        ),
        (
            "12",
            3,
            ["left", "right-top", "right-bottom"],
            ["left", "right-top", "right-bottom"],
        ),
        (
            "13",
            4,
            ["left", "right-top", "right-mid", "right-bottom"],
            ["left", "right-top", "right-mid", "right-bottom"],
        ),
        (
            "21",
            3,
            ["left-top", "right", "left-bottom"],
            ["left-top", "right", "left-bottom"],
        ),
        (
            "22",
            4,
            ["left-top", "right-top", "right-bottom", "left-bottom"],
            ["left-top", "right-top", "left-bottom", "right-bottom"],
        ),
        (
            "31",
            4,
            ["left-top", "right", "left-mid", "left-bottom"],
            ["left-top", "right", "left-mid", "left-bottom"],
        ),
        (
            "111",
            3,
            ["left", "middle", "right"],
            ["left", "middle", "right"],
        ),
        (
            "122",
            5,
            ["left", "middle-top", "right-top", "right-bottom", "middle-bottom"],
            ["left", "middle-top", "right-top", "middle-bottom", "right-bottom"],
        ),
        (
            "221",
            5,
            ["left-top", "middle-top", "right", "left-bottom", "middle-bottom"],
            ["left-top", "middle-top", "right", "left-bottom", "middle-bottom"],
        ),
    ]

    def test_layout_data_table_contract(self):
        """LAYOUT_CONTRACTS 表本身满足视觉位置契约。

        这是静态测试，先于任何 bash 脚本调用验证表数据无错；
        防止后续修改时引入 typo 或重复。
        """
        for code, pane_count, creation_positions, visual_positions in self.LAYOUT_CONTRACTS:
            with self.subTest(layout=code):
                # 视觉位置总数 = pane 总数
                self.assertEqual(
                    len(visual_positions),
                    pane_count,
                    msg=f"{code}: 视觉位置数 ({len(visual_positions)}) ≠ pane 总数 ({pane_count})",
                )
                # 创建位置总数 = pane 总数
                self.assertEqual(
                    len(creation_positions),
                    pane_count,
                    msg=f"{code}: 创建位置数 ({len(creation_positions)}) ≠ pane 总数 ({pane_count})",
                )
                # 视觉位置无重复
                self.assertEqual(
                    len(set(visual_positions)),
                    pane_count,
                    msg=f"{code}: 视觉位置有重复: {visual_positions}",
                )
                # 创建位置无重复
                self.assertEqual(
                    len(set(creation_positions)),
                    pane_count,
                    msg=f"{code}: 创建位置有重复: {creation_positions}",
                )
                # 创建位置与视觉位置集合相等
                self.assertEqual(
                    set(creation_positions),
                    set(visual_positions),
                    msg=(
                        f"{code}: 创建与视觉位置集合不一致\n"
                        f"  creation: {set(creation_positions)}\n"
                        f"  visual:   {set(visual_positions)}\n"
                        f"  diff:     {set(creation_positions) ^ set(visual_positions)}"
                    ),
                )

    def test_hopen_once_dispatch_lands_on_visual_positions_for_all_layouts(self):
        """hopen-once.sh 按视觉阅读顺序派位（覆盖所有 9 个 layout）。

        对每个 layout 跑 hopen-once.sh with K:N 个 kind，验证：
          - 派位总数 = pane 总数
          - agent name 按视觉位置顺序排列
          - 派位的 pane id 集覆盖 workspace 的所有 pane

        这是 hopen-once.sh 对 _position_for 反查的正确性测试：
        _h_row_major 给出的视觉位置必须能找到 _position_for 对应的 created pane。
        """
        for code, pane_count, _creation, visual_positions in self.LAYOUT_CONTRACTS:
            with self.subTest(layout=code):
                self.clear_calls()
                ws_id = f"w-void-{code}"
                result = self.run_launcher(
                    HOPEN_ONCE,
                    "-l",
                    code,
                    f"codex:{pane_count}",
                    FAKE_WORKSPACE_ID=ws_id,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"{code}: hopen-once.sh 退出码 {result.returncode}\n{result.stderr}",
                )

                calls = self.read_calls()
                starts = self.calls_for(calls, "agent", "start")
                target_panes = [call[call.index("--pane") + 1] for call in starts]
                names = [call[2] for call in starts]

                # 派位总数 = pane 总数
                self.assertEqual(
                    len(starts),
                    pane_count,
                    msg=f"{code}: 派位 {len(starts)} 次，应为 {pane_count}",
                )

                # agent name 按视觉位置顺序排列
                expected_names = [f"hopen-{ws_id}-{p}" for p in visual_positions]
                self.assertEqual(
                    names,
                    expected_names,
                    msg=(
                        f"{code}: agent name 顺序不对\n"
                        f"  expected: {expected_names}\n"
                        f"  actual:   {names}"
                    ),
                )

                # 派位的 pane id 集 = workspace 的所有 pane
                expected_panes = {f"{ws_id}:p{i + 1}" for i in range(pane_count)}
                self.assertEqual(
                    set(target_panes),
                    expected_panes,
                    msg=(
                        f"{code}: 派位 pane 集不对\n"
                        f"  expected: {expected_panes}\n"
                        f"  actual:   {set(target_panes)}"
                    ),
                )

    def test_hopen_and_hopen_once_kind_to_pane_mapping_match_for_all_layouts(self):
        """hopen.sh (conf) 与 hopen-once.sh (CLI) 给出相同的 kind → pane 映射。

        这验证两个派位入口对"视觉位置 i"概念的契约一致性：
        - hopen-once.sh 用户按视觉顺序传 KINDS[]
        - hopen.sh 用户在 conf 里按视觉位置名写 kind
        两种方式表达"视觉位置 i 的 kind"，最终每个 pane 分到的 kind 必须相同。

        例如 layout 22：
          - hopen-once.sh 用 [K0, K1, K2, K3] → p1=K0, p2=K1, p4=K2, p3=K3
            （按视觉 left-top, right-top, left-bottom, right-bottom 派位）
          - hopen.sh 用 conf [left-top=K0, right-top=K1, left-bottom=K2, right-bottom=K3]
            → 按创建 idx 0..3 派位：p1=K0, p2=K1, p3=K3, p4=K2
          两个 {pane: kind} 映射都给出 {p1:K0, p2:K1, p3:K3, p4:K2}
        """
        kinds_pool = ["codex", "pi", "claude", "hermes", "opencode"]

        for code, pane_count, _creation, visual_positions in self.LAYOUT_CONTRACTS:
            with self.subTest(layout=code):
                # 清上次迭代的 log 与 state，避免 pane id 跨迭代错位
                self.clear_calls()
                kinds = [kinds_pool[i % len(kinds_pool)] for i in range(pane_count)]
                ws_id = f"w-map-{code}"

                # 跑 hopen-once.sh：kinds 按视觉顺序传
                result_once = self.run_launcher(
                    HOPEN_ONCE,
                    "-l",
                    code,
                    *kinds,
                    FAKE_WORKSPACE_ID=ws_id,
                )
                self.assertEqual(
                    result_once.returncode,
                    0,
                    msg=f"{code}: hopen-once.sh 失败\n{result_once.stderr}",
                )
                calls_once = self.read_calls()
                once_kind_pane = {
                    call[call.index("--pane") + 1]: call[call.index("--kind") + 1]
                    for call in self.calls_for(calls_once, "agent", "start")
                }

                # 清状态，准备 hopen.sh
                self.clear_calls()
                sections = []
                for pos, kind in zip(visual_positions, kinds):
                    sections.append(f'[layout.{code}.panes.{pos}]\nkind = "{kind}"')
                self.write_conf("\n\n".join(sections))
                result_conf = self.run_launcher(HOPEN, code, FAKE_WORKSPACE_ID=ws_id)
                self.assertEqual(
                    result_conf.returncode,
                    0,
                    msg=f"{code}: hopen.sh 失败\n{result_conf.stderr}",
                )
                calls_conf = self.read_calls()
                conf_kind_pane = {
                    call[call.index("--pane") + 1]: call[call.index("--kind") + 1]
                    for call in self.calls_for(calls_conf, "agent", "start")
                }

                self.assertEqual(
                    once_kind_pane,
                    conf_kind_pane,
                    msg=(
                        f"{code}: kind → pane 映射两个入口不一致\n"
                        f"  visual_positions:  {visual_positions}\n"
                        f"  kinds:             {kinds}\n"
                        f"  hopen-once.sh map: {once_kind_pane}\n"
                        f"  hopen.sh map:      {conf_kind_pane}"
                    ),
                )


class ThreePaneColumnRatioTests(FakeHerdrLauncherTests):
    """`13` / `31` 的三 pane 列必须走显式 ratio，而不是连续两次对半切。

    连续对半切会把那一列切成 50% / 25% / 25%，与其它布局「每列等分」的
    视觉契约不符。做法是先切走 1/3（0.333），再把剩下的 2/3 对半切（0.5）。
    实测 40 行 → 13 / 14 / 13。
    """

    def split_ratios(self, code):
        # 日志是追加写的，跑之前清掉，否则上一个 layout 的 split 会混进来
        self.log_path.unlink(missing_ok=True)
        result = self.run_launcher(HOPEN_SH, code, "-n")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        ratios = []
        for call in self.calls_for(self.read_calls(), "pane", "split"):
            ratios.append(call[call.index("--ratio") + 1] if "--ratio" in call else None)
        return ratios

    def test_13_and_31_pass_explicit_ratio_for_the_three_pane_column(self):
        for code in ("13", "31"):
            with self.subTest(layout=code):
                self.assertEqual(
                    self.split_ratios(code),
                    [None, "0.333", "0.5"],
                    msg=f"{code}: 第一步是左右对半列，不带 ratio；第二三步是三 pane 列",
                )

    def test_layouts_without_explicit_ratios(self):
        """这些布局保留默认比例；221 另用 ratio 保证单 pane 与四宫格各占一半。"""
        for code in ("11", "12", "21", "22", "111", "122"):
            with self.subTest(layout=code):
                ratios = self.split_ratios(code)
                self.assertNotIn("0.333", ratios)
                self.assertNotIn("0.5", ratios)
                self.assertTrue(all(r is None for r in ratios), msg=f"{code}: {ratios}")


class LauncherArgumentTests(FakeHerdrLauncherTests):
    """hopen.sh 的参数解析与 --help 输出。"""

    def write_conf_with_kind(self):
        self.write_conf(
            """
            [layout.11.panes.left]
            kind = "pi"
            """
        )

    def test_no_agents_flag_is_honoured_after_the_layout_code(self):
        """`hopen.sh <code> [-n]` 是头部注释声明的用法。

        参数解析器曾在遇到第一个非选项参数（layout 代号）时 break，
        导致写在代号后面的 -n 被**静默忽略** —— 该跳过的 agent 被真的启起来。
        """
        self.write_conf_with_kind()

        with_flag = self.run_launcher(HOPEN_SH, "11", "-n")
        self.assertEqual(with_flag.returncode, 0, msg=with_flag.stderr)
        self.assertEqual(
            self.calls_for(self.read_calls(), "agent", "start"),
            [],
            msg="代号后面的 -n 必须生效",
        )

        self.log_path.unlink(missing_ok=True)
        without_flag = self.run_launcher(HOPEN_SH, "11")
        self.assertEqual(without_flag.returncode, 0, msg=without_flag.stderr)
        self.assertEqual(
            len(self.calls_for(self.read_calls(), "agent", "start")),
            1,
            msg="不给 -n 时应按 conf 派位",
        )

    def test_extra_positional_argument_is_rejected(self):
        result = self.run_launcher(HOPEN_SH, "11", "22")
        self.assertEqual(result.returncode, 2)
        self.assertIn("多余参数", result.stderr)

    def test_help_prints_the_whole_header_comment_block(self):
        """--help 不能靠写死的行号截断头部注释。

        曾用 `sed -n '2,19p'`，而头部注释早已长到 31 行，后半段用法说明
        和 6 条设计原则被静默吃掉。
        """
        for script, last_line in (
            (HOPEN_SH, "#   6. 成功创建的 ws 不由脚本自动清理"),
            (HOPEN_ONCE_SH, "#   - Kinds placed in row-major visual order"),
        ):
            with self.subTest(script=script.name):
                result = self.run_launcher(script, "--help")
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                self.assertIn(last_line, result.stdout)


if __name__ == "__main__":
    unittest.main()
