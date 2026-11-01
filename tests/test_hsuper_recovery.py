"""T4 — the recovery ladder.

The ladder is the one in coordinator-prompt.md §5: two original-session
resumes, then one rebuild, then the branch is marked failed.  These tests
drive it with a fake ``herdr`` on PATH that hands out queued verdicts, the
same technique tests/test_hwait.py uses — no test switches in production code.
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


# A fake herdr that answers with a queued verdict per wait round.
#
# One wait round is: `agent get` (resolve the pane), `pane wait-output` (the
# marker signal) and `agent wait` (the lifecycle signal).  The verdict of the
# round is picked when the round starts, so the second marker check that
# hwait performs after a settled agent still sees the same verdict.
FAKE = '''#!/usr/bin/env python3
import fcntl, json, os, re, subprocess, sys

args = sys.argv[1:]
key = " ".join(args[:2])

worker_pid = None


# Parallel dispatch runs two fake invocations at once; a state file written
# read-modify-write without a lock loses updates and a torn read crashes the
# fake (invalid JSON).  Each invocation holds an exclusive lock for its
# lifetime, which serializes the fake at command granularity.
state_lock_handle = open(os.environ["HSUPER_FAKE_STATE"], "a+")
fcntl.flock(state_lock_handle, fcntl.LOCK_EX)
state_lock_handle.seek(0)
raw = state_lock_handle.read()
state = json.loads(raw) if raw.strip() else {}


def _state():
    return state


def store():
    state_lock_handle.seek(0)
    state_lock_handle.truncate()
    json.dump(state, state_lock_handle)
    state_lock_handle.flush()


def worker_alive():
    pid = _state().get("worker_pid")
    if not pid:
        return False
    # os.kill(pid, 0) also succeeds for a zombie (nobody reaped it), so the
    # process state decides: Z means dead for pane/agent purposes.
    try:
        out = subprocess.run(["ps", "-o", "state=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=10)
    except OSError:
        return False
    state = out.stdout.strip()
    return bool(state) and "Z" not in state


log_path = os.environ.get("HSUPER_FAKE_LOG")
if log_path:
    import time as _time
    with open(log_path, "a") as log:
        log.write(json.dumps([round(_time.time(), 2)] + args) + "\\n")

state_path = os.environ["HSUPER_FAKE_STATE"]
verdicts = state.get("verdicts", [])
index = state.get("index", 0)


def respond(payload, status=0):
    print(json.dumps(payload))
    raise SystemExit(status)


def current():
    # Once the queue is exhausted the worker behaves normally and finishes.
    if index >= len(verdicts):
        return "marker"
    return verdicts[index]


def write_result(status_text, step=None):
    """The worker writes its result file when it settles.

    The step defaults to the shared ``state['step']``; parallel dispatch
    passes the step resolved from the wait target instead, since two agents
    interleave their prompts and the shared field would land one branch's
    result in the other's file.
    """
    run_dir = state.get("run_dir")
    step = step or state.get("step")
    if not (run_dir and step):
        return
    result_dir = os.path.join(run_dir, "result")
    os.makedirs(result_dir, exist_ok=True)
    payload = {"status": status_text, "summary": "fake %s" % status_text}
    with open(os.path.join(result_dir, step + ".json"), "w") as handle:
        json.dump(payload, handle)


if key == "agent get":
    if not state.get("agent_alive", True) or not worker_alive():
        respond({"error": {"code": "agent_not_found", "message": "not found"}}, 1)
    # Starting a round: pick its verdict without consuming it yet.
    state["round_verdict"] = current()
    store()
    target = args[2] if len(args) > 2 else "worker"
    pane = state.get("agent_panes", {}).get(target) or state.get("agent_pane", "w1:p1")
    respond({"result": {"agent": {"pane_id": pane,
                                 "name": target, "agent_status": "working"}}})

def step_for_pane(pane):
    """The step whose worker pane is ``pane`` (agent names end in the step)."""
    for name, agent_pane in state.get("agent_panes", {}).items():
        if agent_pane == pane:
            return name.rsplit("-", 1)[-1]
    return None


if key == "pane wait-output":
    pane = args[2] if len(args) > 2 else "w1:p1"
    if state.get("round_verdict") == "marker":
        write_result("done", step_for_pane(pane))
        respond({"result": {"matched_line": "DONE %s" % (step_for_pane(pane) or "dev"),
                            "pane_id": pane}})
    respond({"error": {"code": "timeout", "message": "no marker"}}, 1)

if key == "agent wait":
    verdict = state.get("round_verdict", current())
    index += 1
    # A variant used to pin the settled+gate-failure regression: the
    # worker reports done but the expect evidence is missing.
    is_settled_done = verdict == "settled_done"
    state["index"] = index
    if verdict in ("settled", "unknown"):
        write_result("failed")
    if is_settled_done:
        write_result("done")
    store()
    if verdict == "gone":
        respond({"error": {"code": "agent_not_found", "message": "gone"}}, 1)
    if verdict == "blocked":
        respond({"result": {"agent": {"agent_status": "blocked", "pane_id": "w1:p1"}}})
    if verdict == "unknown":
        respond({"result": {"agent": {"agent_status": "unknown", "pane_id": "w1:p1"}}})
    if verdict == "timeout":
        respond({"error": {"code": "timeout", "message": "still working"}}, 1)
    respond({"result": {"agent": {"agent_status": "idle", "pane_id": "w1:p1"}}})

if key == "agent start":
    # Parallel dispatch runs two workers: record each agent's pane by name,
    # or the shared single field would make hwait's pane check fail for one
    # of them and drag both into the recovery ladder.
    target = args[2] if len(args) > 2 else "worker"
    if "--pane" in args:
        state.setdefault("agent_panes", {})[target] = args[args.index("--pane") + 1]
    state["agent_pane"] = args[args.index("--pane") + 1] if "--pane" in args \
        else state.get("agent_pane", "w1:p1")
    # A started agent is a real process: spawn a stand-in so the kill /
    # process-info / agent-get liveness semantics also hold for rebuilds.
    # The streams must be detached: the caller captures this script's stdout
    # and a child holding the pipe would keep that capture open forever.
    worker = subprocess.Popen(["sleep", "300"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state["worker_pid"] = str(worker.pid)
    state["agent_alive"] = True
    store()
    respond({"result": {"type": "agent_started"}})

if key == "agent prompt":
    # The worker is told to read prompts/<step>.md; the fake uses that path
    # to learn where the run directory is, so it can write the result file
    # the way a real worker would.
    text = args[3] if len(args) > 3 else ""
    match = re.search(r"(\\S*?)/prompts/([A-Za-z0-9_-]+)\\.md", text)
    if match:
        state["run_dir"] = match.group(1)
        state["step"] = match.group(2)
        store()
    respond({"result": {"type": "prompt_submitted"}})

if key == "agent send-keys":
    # ctrl+c is how a stopped agent releases its name.
    if "ctrl+c" in args:
        state["agent_alive"] = False
        store()
    respond({"result": {"type": "ok"}})

if key == "pane process-info":
    # The fake worker is a real process (spawned on agent start): while it
    # lives the pane shows pi in the foreground; once killed, the pane is
    # back at the shell prompt — the measured semantics of a killed agent.
    foreground = [{"argv0": "zsh", "pid": 100, "name": "zsh",
                   "argv": ["-zsh"], "cmdline": "-zsh", "cwd": "/tmp"}]
    pid = _state().get("worker_pid")
    if pid and worker_alive():
        foreground.append({"argv0": "pi", "pid": int(pid),
                           "name": "pi", "argv": ["pi"],
                           "cmdline": "pi", "cwd": "/tmp"})
    respond({"result": {"process_info": {"shell_pid": 100,
                                         "foreground_processes": foreground}}})

if key == "pane read":
    sys.stdout.write("worker output\\n")
    raise SystemExit(0)

if key == "notification show":
    respond({"result": {"type": "notification_show"}})

if key == "workspace create":
    respond({"result": {"type": "workspace_created",
                        "workspace": {"workspace_id": "w1", "label": "fake",
                                      "active_tab_id": "w1:t1", "pane_count": 1,
                                      "tab_count": 1, "agent_status": "unknown",
                                      "focused": False, "number": 1},
                        "root_pane": {"pane_id": "w1:p1", "tab_id": "w1:t1",
                                      "workspace_id": "w1", "cwd": "/tmp",
                                      "agent_status": "unknown", "focused": False},
                        "tab": {"tab_id": "w1:t1", "workspace_id": "w1",
                                "label": "1", "number": 1, "pane_count": 1,
                                "agent_status": "unknown", "focused": False}}})

if key == "tab create":
    respond({"result": {"type": "tab_created",
                        "tab": {"tab_id": "w1:t9", "workspace_id": "w1",
                                "label": "fake", "number": 9, "pane_count": 1,
                                "agent_status": "unknown", "focused": False},
                        "root_pane": {"pane_id": "w1:p9", "tab_id": "w1:t9",
                                      "workspace_id": "w1", "cwd": "/tmp",
                                      "agent_status": "unknown", "focused": False}}})

if key == "pane split":
    # The counter must advance: two splits returning the same pane id would
    # map two steps onto one pane, and the step-for-pane reverse lookup in
    # the wait handler would write one branch's result into the other's file.
    splits = state.setdefault("splits", [])
    pane_id = "w1:p%d" % (10 + len(splits))
    splits.append(pane_id)
    store()
    respond({"result": {"type": "pane_info",
                        "pane": {"pane_id": pane_id,
                                 "tab_id": "w1:t9", "workspace_id": "w1",
                                 "cwd": "/tmp", "agent_status": "unknown",
                                 "focused": False}}})

if key == "pane layout":
    panes = [{"pane_id": "w1:p1", "rect": {"x": 0, "y": 0, "width": 60, "height": 40}},
             {"pane_id": "w1:p2", "rect": {"x": 60, "y": 0, "width": 60, "height": 40}},
             {"pane_id": "w1:p3", "rect": {"x": 0, "y": 0, "width": 60, "height": 40}}]
    respond({"result": {"type": "pane_layout",
                        "layout": {"workspace_id": "w1", "tab_id": "w1:t9",
                                   "focused_pane_id": "w1:p1", "zoomed": False,
                                   "area": {"x": 0, "y": 0, "width": 120, "height": 40},
                                   "panes": panes, "splits": []}}})

if key in ("pane rename", "tab rename"):
    respond({"result": {"type": "ok"}})

respond({"error": {"code": "unknown_fake_command", "message": " ".join(args)}}, 1)
'''


FLOW = """
[flow.sample]
description = "测试"
# The fake herdr's verdict queue is shared across steps, so the e2e tests
# need the strictly serial dispatch path to keep the sequences deterministic.
max_parallel = 1

[flow.sample.defaults]
kind = "codex"

# dev and review are independent branches; commit depends on dev.
[flow.sample.steps.dev]
role = "实现"
prompt = "任务：{{task}}\\n打印 {{marker}}"
expect = ["{{run_dir}}/result/dev.json"]
next = ["commit"]

[flow.sample.steps.review]
role = "审查"
prompt = "审查 {{step}}"
expect = ["{{run_dir}}/result/review.json"]

[flow.sample.steps.commit]
role = "提交"
prompt = "提交 {{step}}"
expect = ["{{run_dir}}/result/commit.json"]
"""


class LadderUnitTests(unittest.TestCase):
    """The ladder decision table, on its own."""

    def test_marker_always_advances(self):
        self.assertEqual(hsuper.ladder_action(0, 0, "marker", 2, 1), "advance")

    def test_blocked_is_its_own_outcome(self):
        self.assertEqual(hsuper.ladder_action(0, 0, "blocked", 2, 1), "blocked")

    def test_settled_resumes_first(self):
        self.assertEqual(hsuper.ladder_action(0, 0, "settled", 2, 1), "resume")
        self.assertEqual(hsuper.ladder_action(1, 0, "settled", 2, 1), "resume")

    def test_after_two_resumes_the_step_rebuilds(self):
        self.assertEqual(hsuper.ladder_action(2, 0, "settled", 2, 1), "rebuild")

    def test_after_the_rebuild_the_step_fails(self):
        self.assertEqual(hsuper.ladder_action(2, 1, "settled", 2, 1), "fail")

    def test_gone_goes_straight_to_rebuild(self):
        # The session is gone; resuming it is not possible.
        self.assertEqual(hsuper.ladder_action(0, 0, "gone", 2, 1), "rebuild")

    def test_gone_after_the_rebuild_fails(self):
        self.assertEqual(hsuper.ladder_action(0, 1, "gone", 2, 1), "fail")

    def test_timeout_and_unknown_use_the_resume_path(self):
        self.assertEqual(hsuper.ladder_action(0, 0, "timeout", 2, 1), "resume")
        self.assertEqual(hsuper.ladder_action(0, 0, "unknown", 2, 1), "resume")

    def test_the_ladder_numbers_are_the_documented_ones(self):
        self.assertEqual(hsuper.LADDER_RESUMES, 2)
        self.assertEqual(hsuper.LADDER_REBUILDS, 1)


class RecoveryRunTests(unittest.TestCase):
    """The ladder driven end to end through a fake herdr."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.bin_dir = self.folder / "bin"
        self.bin_dir.mkdir()
        fake = self.bin_dir / "herdr"
        fake.write_text(FAKE)
        fake.chmod(0o755)
        # The fake spawns its own stand-in process on `agent start` (so the
        # kill / process-info liveness semantics also hold for rebuilds); the
        # teardown reaps whatever run left behind.
        self.state_path = self.folder / "state.json"
        self.log_path = self.folder / "calls.jsonl"
        self.env = os.environ.copy()
        self.env.update(PATH=str(self.bin_dir) + os.pathsep + self.env["PATH"],
                        HSUPER_FAKE_STATE=str(self.state_path),
                        HSUPER_FAKE_LOG=str(self.log_path))

    def tearDown(self):
        self.reap_fake_workers()
        self.directory.cleanup()

    def reap_fake_workers(self):
        if not self.state_path.exists():
            return
        for run_state in ([json.loads(self.state_path.read_text())]
                          if self.state_path.stat().st_size else []):
            pid = run_state.get("worker_pid")
            if pid:
                subprocess.run(["kill", "-9", str(pid)], capture_output=True)

    def verdicts(self, *items):
        self.state_path.write_text(json.dumps({"verdicts": list(items), "index": 0}))

    def calls(self):
        if not self.log_path.exists():
            return []
        # Each logged line carries a leading timestamp (added for timing
        # analysis); drop it so callers can filter by ["agent", "start"].
        return [json.loads(line)[1:] for line in self.log_path.read_text().splitlines()]

    def run_flow(self, verdicts, task="任务", flow_text=None):
        self.verdicts(*verdicts)
        flow_path = self.folder / "sample.toml"
        flow_path.write_text(flow_text or FLOW, encoding="utf-8")
        entry = SCRIPTS / "hsuper.py"
        result = subprocess.run(
            [sys.executable, str(entry), "--flow-file", str(flow_path),
             "--task", task, "--foreground", "--path", str(self.folder)],
            env=self.env, capture_output=True, text=True, cwd=str(self.folder),
            timeout=180)
        return result

    def run_dirs(self):
        root = self.folder / ".herdr-recipes" / "runs"
        if not root.is_dir():
            return []
        return sorted(root.iterdir())

    def state_of(self, run_dir):
        return json.loads((run_dir / "state.json").read_text())

    # --- the four acceptance sequences -----------------------------------

    def test_gone_then_marker_rebuilds_exactly_once(self):
        result = self.run_flow(["gone", "marker"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run_dir = self.run_dirs()[0]
        state = self.state_of(run_dir)
        attempts = state["steps"]["dev"]["attempts"]
        self.assertEqual(attempts["rebuild"], 1)
        self.assertEqual(attempts["resume"], 0)
        self.assertEqual(state["steps"]["dev"]["status"], "done")

    def test_three_failures_rebuild_after_two_resumes(self):
        result = self.run_flow(["settled", "settled", "settled", "marker"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run_dir = self.run_dirs()[0]
        state = self.state_of(run_dir)
        attempts = state["steps"]["dev"]["attempts"]
        self.assertEqual(attempts["resume"], 2)
        self.assertEqual(attempts["rebuild"], 1)
        self.assertEqual(state["steps"]["dev"]["status"], "done")

    def test_four_failures_fail_the_branch_and_keep_other_branches(self):
        result = self.run_flow(["settled", "settled", "settled", "settled"])
        self.assertEqual(result.returncode, 20, result.stdout + result.stderr)
        run_dir = self.run_dirs()[0]
        state = self.state_of(run_dir)
        self.assertEqual(state["steps"]["dev"]["status"], "failed")
        self.assertEqual(state["steps"]["dev"]["attempts"]["resume"], 2)
        self.assertEqual(state["steps"]["dev"]["attempts"]["rebuild"], 1)
        # The other branch is not a dependant of dev, so it still runs.
        self.assertEqual(state["steps"]["review"]["status"], "done")
        # A dependant of the failed branch is skipped, not attempted.
        self.assertEqual(state["steps"]["commit"]["status"], "skipped")

    def test_settled_done_with_failing_gate_does_not_spin_forever(self):
        # Regression for the settled-branch infinite loop: result says done
        # but the expect evidence is missing, the agent sits idle, so every
        # re-armed wait returns settled again.  The re-arm bound must turn
        # this into the recovery ladder, not a spin.
        flow = FLOW.replace('expect = ["{{run_dir}}/result/dev.json"]',
                            'expect = ["{{run_dir}}/result/never.json"]')
        result = self.run_flow(["settled_done"] * 6, flow_text=flow)
        self.assertEqual(result.returncode, 20, result.stdout + result.stderr)
        state = self.state_of(self.run_dirs()[0])
        self.assertEqual(state["steps"]["dev"]["status"], "failed")
        attempts = state["steps"]["dev"].get("attempts", {})
        self.assertEqual(attempts.get("resume"), 2)
        self.assertEqual(attempts.get("rebuild"), 1)

    def test_blocked_marks_the_branch_and_keeps_other_branches(self):
        result = self.run_flow(["blocked", "marker"])
        self.assertEqual(result.returncode, 21, result.stdout + result.stderr)
        run_dir = self.run_dirs()[0]
        state = self.state_of(run_dir)
        self.assertEqual(state["steps"]["dev"]["status"], "blocked")
        self.assertEqual(state["steps"]["review"]["status"], "done")
        self.assertEqual(state["steps"]["commit"]["status"], "skipped")

    # --- evidence ---------------------------------------------------------

    def test_resume_uses_the_original_session(self):
        result = self.run_flow(["settled", "settled", "settled", "marker"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        starts = [c for c in self.calls()
                  if c[:2] == ["agent", "start"] and c[2].startswith("hsuper-")
                  and c[2].endswith("-dev")]
        prompts = [c for c in self.calls()
                   if c[:2] == ["agent", "prompt"] and c[2].endswith("-dev")]
        # One start (the initial one) plus one rebuild; the two resumes are
        # prompts to the same session, not new agents.
        self.assertEqual(len(starts), 2)
        self.assertEqual(len(prompts), 4)

    def test_a_resume_prompt_never_contains_the_marker(self):
        self.run_flow(["settled", "settled", "settled", "marker"])
        prompts = [c for c in self.calls() if c[:2] == ["agent", "prompt"]]
        for prompt in prompts:
            self.assertNotIn("DONE r1-dev", " ".join(prompt))

    def test_the_rebuild_handoff_is_written(self):
        self.run_flow(["gone", "marker"])
        run_dir = self.run_dirs()[0]
        handoff = run_dir / "handoff" / "dev-rebuild.md"
        self.assertTrue(handoff.is_file())
        self.assertIn("dev", handoff.read_text())

    def test_the_prompt_file_carries_the_marker(self):
        self.run_flow(["marker"])
        run_dir = self.run_dirs()[0]
        prompt = run_dir / "prompts" / "dev.md"
        self.assertTrue(prompt.is_file())
        self.assertIn("DONE ", prompt.read_text())

    def test_the_submitted_prompt_only_names_the_prompt_file(self):
        self.run_flow(["marker"])
        prompts = [c for c in self.calls() if c[:2] == ["agent", "prompt"]]
        self.assertTrue(prompts)
        text = prompts[0][3]
        self.assertIn("prompts/dev.md", text)
        self.assertNotIn("DONE", text)


if __name__ == "__main__":
    unittest.main()


PARALLEL_FLOW = """
[flow.par]
description = "并行"

[flow.par.defaults]
kind = "codex"

# Two independent branches; parallel dispatch runs them concurrently.
[flow.par.steps.one]
role = "一"
prompt = "任务：{{task}}\\n打印 {{marker}}"
expect = ["{{run_dir}}/result/one.json"]

[flow.par.steps.two]
role = "二"
prompt = "任务：{{task}}\\n打印 {{marker}}"
expect = ["{{run_dir}}/result/two.json"]
"""


class ParallelDispatchTests(unittest.TestCase):
    """max_parallel > 1 dispatches independent steps concurrently.

    The fake herdr answers every round with a marker (an exhausted queue
    behaves normally), so both branches complete without recovery.
    """

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.bin_dir = self.folder / "bin"
        self.bin_dir.mkdir()
        fake = self.bin_dir / "herdr"
        fake.write_text(FAKE)
        fake.chmod(0o755)
        self.state_path = self.folder / "state.json"
        self.log_path = self.folder / "calls.jsonl"
        self.env = os.environ.copy()
        self.env.update(PATH=str(self.bin_dir) + os.pathsep + self.env["PATH"],
                        HSUPER_FAKE_STATE=str(self.state_path),
                        HSUPER_FAKE_LOG=str(self.log_path))

    def tearDown(self):
        self.reap_fake_workers()
        self.directory.cleanup()

    def reap_fake_workers(self):
        if not self.state_path.exists():
            return
        for run_state in ([json.loads(self.state_path.read_text())]
                          if self.state_path.stat().st_size else []):
            pid = run_state.get("worker_pid")
            if pid:
                subprocess.run(["kill", "-9", str(pid)], capture_output=True)

    def verdicts(self, *items):
        self.state_path.write_text(json.dumps({"verdicts": list(items), "index": 0}))

    def calls(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line)[1:] for line in self.log_path.read_text().splitlines()]

    def run_dirs(self):
        root = self.folder / ".herdr-recipes" / "runs"
        if not root.is_dir():
            return []
        return sorted(root.iterdir())

    def state_of(self, run_dir):
        return json.loads((run_dir / "state.json").read_text())

    def run_flow(self, flow_text=PARALLEL_FLOW, task="任务"):
        self.verdicts("marker")
        flow_path = self.folder / "par.toml"
        flow_path.write_text(flow_text, encoding="utf-8")
        entry = SCRIPTS / "hsuper.py"
        return subprocess.run(
            [sys.executable, str(entry), "--flow-file", str(flow_path),
             "--task", task, "--foreground", "--path", str(self.folder)],
            env=self.env, capture_output=True, text=True, cwd=str(self.folder),
            timeout=180)

    def test_independent_steps_run_concurrently_and_both_finish(self):
        result = self.run_flow()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run_dir = self.run_dirs()[0]
        state = self.state_of(run_dir)
        self.assertEqual(state["steps"]["one"]["status"], "done")
        self.assertEqual(state["steps"]["two"]["status"], "done")
        # Both workers were started without waiting for the other branch.
        # The --help call from agent_kinds shares the ["agent", "start"] prefix.
        starts = [c for c in self.calls()
                  if c[:2] == ["agent", "start"] and len(c) > 2
                  and "--help" not in c]
        self.assertEqual(len(starts), 2)

    def test_serial_path_still_waits_between_dispatches(self):
        # max_parallel = 1 serializes: the second step must not start before
        # the first one finished, so a wait round sits between the two starts.
        serial = PARALLEL_FLOW.replace("[flow.par]", "[flow.par]\nmax_parallel = 1")
        self.assertEqual(self.run_flow(flow_text=serial).returncode, 0)
        calls = self.calls()
        starts = [i for i, c in enumerate(calls)
                  if c[:2] == ["agent", "start"] and len(c) > 2
                  and "--help" not in c]
        self.assertEqual(len(starts), 2)
        # Serial: one's wait round (its first agent get) happens before the
        # second step dispatches, so the workers never overlap.
        one_wait = next(i for i, c in enumerate(calls)
                        if c[:2] == ["agent", "get"] and "one" in c[2])
        self.assertLess(one_wait, starts[1])


if __name__ == "__main__":
    unittest.main()
