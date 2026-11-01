"""Opt-in live hsuper smoke on a temporary isolated Herdr server.

Runs the real supervisor against real pi workers: a one-step happy flow,
an injected persistent failure (ladder to exit 20 while the independent
branch still done), and --dry-run (which must not create anything).
Requires authenticated `pi` on this machine.  Add --kill-gone to also
verify the gone→rebuild ladder.

Run with --live-agents --binary /path/to/herdr --output /tmp/hsuper-smoke.json.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--binary", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--timeout", type=int, default=420,
                    help="seconds per scenario")
parser.add_argument("--live-agents", action="store_true",
                    help="explicitly allow real model calls")
parser.add_argument("--kill-gone", action="store_true",
                    help="also verify the gone→rebuild ladder (SIGKILLs a worker)")
parser.add_argument("--keep", action="store_true",
                    help="keep the temp directory for inspection")
args = parser.parse_args()
if not args.live_agents:
    parser.error("this smoke requires explicit --live-agents")

repo = Path(__file__).resolve().parents[1]
binary = str(Path(args.binary).resolve())

report = {"server": "isolated", "scenarios": {}}
flow = """
[flow.smoke]
description = "smoke"

[flow.smoke.defaults]
kind = "pi"

[flow.smoke.steps.alpha]
role = "A"
prompt = \"\"\"
任务：写入 {{run_dir}}/result/alpha.json，内容 {"status":"done","note":"alpha"}。
然后在终端单独一行打印：{{marker}}。
不要修改项目文件。
\"\"\"
expect = ["{{run_dir}}/result/alpha.json"]

[flow.smoke.steps.bravo]
role = "B"
prompt = \"\"\"
任务：写入 {{run_dir}}/result/bravo.json，内容 {"status":"done","note":"bravo"}。
然后在终端单独一行打印：{{marker}}。
不要修改项目文件。
\"\"\"
expect = ["{{run_dir}}/result/bravo.json"]
"""

kill_flow = """
[flow.smokegone]
description = "gone ladder"

[flow.smokegone.defaults]
kind = "pi"

[flow.smokegone.steps.solo]
role = "S"
prompt = \"\"\"
任务：先用 shell 执行 sleep 90，然后写入 {{run_dir}}/result/solo.json，
内容 {"status":"done","note":"solo"}。然后在终端单独一行打印：{{marker}}。
不要修改项目文件。
\"\"\"
expect = ["{{run_dir}}/result/solo.json"]
next = ["after"]

[flow.smokegone.steps.after]
role = "T"
prompt = \"\"\"
任务：写入 {{run_dir}}/result/after.json，内容 {"status":"done","note":"after"}。
然后在终端单独一行打印：{{marker}}。
不要修改项目文件。
\"\"\"
expect = ["{{run_dir}}/result/after.json"]
"""


def expect(condition, message, report_key):
    if not condition:
        report["scenarios"][report_key] = {"ok": False, "why": message}
        raise AssertionError(message)


def close_workspace_of(run_dir, cli):
    """Close the run's workspace right after its scenario.

    Live agents pile up across scenarios otherwise: the fourth scenario then
    starts a worker on a loaded machine and agent start times out (measured).
    Closing the workspace kills its agents with it.
    """
    state = json.loads((Path(run_dir) / "state.json").read_text())
    workspace_id = state.get("workspace_id")
    if workspace_id:
        cli("workspace", "close", workspace_id)


with tempfile.TemporaryDirectory(prefix="hsuper-smoke-", dir="/tmp") as folder:
    if args.keep:
        folder = tempfile.mkdtemp(prefix="hsuper-smoke-", dir="/tmp")
    root = Path(folder)
    project = root / "project"
    project.mkdir()
    config = root / "config.toml"
    config.write_text('[keys]\nprefix="ctrl+b"\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
    env.update(HERDR_SOCKET_PATH=str(root / "s.sock"),
               HERDR_CONFIG_PATH=str(config),
               XDG_CONFIG_HOME=str(root / "config"),
               XDG_STATE_HOME=str(root / "state"))
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "herdr").symlink_to(binary)
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]

    def run(*items, timeout=60):
        return subprocess.run(items, env=env, cwd=str(project),
                              capture_output=True, text=True, timeout=timeout)

    def cli(*items, timeout=60):
        result = run(binary, *items, timeout=timeout)
        if result.returncode:
            raise RuntimeError((items[:3], result.returncode, result.stderr[:400]))
        raw = result.stdout.strip()
        return json.loads(raw).get("result", {}) if raw else {}

    (project / "flows").mkdir()
    (project / "flows" / "smoke.toml").write_text(flow)
    (project / "flows" / "smokegone.toml").write_text(kill_flow)

    def fresh_run_dir(known):
        """The run directory a scenario created (known = pre-run names).

        Two scenarios can start within the same minute, so sorting by name
        is not enough — the run created *after* the scenario started is the
        one whose state carries this scenario's assertions.
        """
        runsp = project / ".herdr-recipes" / "runs"
        if not runsp.is_dir():
            return None
        fresh = sorted(p.name for p in runsp.iterdir()
                       if p.is_dir() and p.name not in known)
        return runsp / fresh[-1] if fresh else None

    def known_runs():
        runsp = project / ".herdr-recipes" / "runs"
        return set(p.name for p in runsp.iterdir()) if runsp.is_dir() else set()

    def statuses_of(run_dir):
        state = json.loads((Path(run_dir) / "state.json").read_text())
        return {k: v.get("status") for k, v in state.get("steps", {}).items()}, state

    def workspace_count():
        return len(cli("workspace", "list")["workspaces"])

    with (root / "server.log").open("w") as log:
        server = subprocess.Popen([binary, "server"], env=env,
                                  stdout=log, stderr=log)
        try:
            for _ in range(100):
                if (root / "s.sock").exists():
                    break
                if server.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read())
                time.sleep(.1)

            # --- scenario 0: --dry-run must not create anything ---------
            key = "dry-run"
            before = workspace_count()
            result = run("python3", str(repo / "scripts" / "hsuper.py"),
                         "--flow", "smoke", "--task", "dry", "--dry-run",
                         timeout=60)
            expect(result.returncode == 0, result.stderr[:300], key)
            expect("未创建任何资源" in result.stdout, result.stdout[-300:], key)
            expect(workspace_count() == before, "dry-run created resources", key)
            report["scenarios"][key] = {"ok": True}

            # --- scenario 1: happy flow, both steps done, exit 0 --------
            key = "happy"
            known = known_runs()
            started = time.time()
            result = run("python3", str(repo / "scripts" / "hsuper.py"),
                         "--flow", "smoke", "--task", "smoke 冒烟",
                         "--foreground", "--json",
                         "--budget-ms", str(args.timeout * 1000 // 2),
                         timeout=args.timeout + 60)
            elapsed = time.time() - started
            expect(result.returncode == 0,
                   "exit=%d stderr=%s" % (result.returncode, result.stderr[:400]), key)
            run_dir = fresh_run_dir(known)
            expect(run_dir is not None, "happy run directory missing", key)
            statuses, state = statuses_of(run_dir)
            expect(statuses == {"alpha": "done", "bravo": "done"}, statuses, key)
            expect(state["schema_version"] == 1, state, key)
            expect((run_dir / "result" / "alpha.json").is_file(), "alpha.json missing", key)
            expect((run_dir / "result" / "bravo.json").is_file(), "bravo.json missing", key)
            report["scenarios"][key] = {"ok": True, "elapsed_s": round(elapsed, 1)}
            close_workspace_of(run_dir, cli)

            # --- scenario 2: injected persistent failure → ladder to exit 20
            # The worker obeys a simple instruction: write status=failed,
            # print no marker, stop.  Every resume/rebuild re-reads the same
            # prompt file, so the ladder must run out: 2 resumes + 1 rebuild,
            # then the branch fails while the independent bravo still done.
            key = "failure"

            fail_flow = flow.replace(
                '[flow.smoke', '[flow.smokefail').replace(
                '任务：写入 {{run_dir}}/result/alpha.json，内容 {"status":"done","note":"alpha"}。\n然后在终端单独一行打印：{{marker}}。',
                '任务：写入 {{run_dir}}/result/alpha.json，内容 {"status":"failed","note":"injected"}。\n这是注入的持续失败：不要打印完成标记，写完文件就停下，不要做其它任何事。')
            (project / "flows" / "smokefail.toml").write_text(fail_flow)
            known = known_runs()
            started = time.time()
            result = run("python3", str(repo / "scripts" / "hsuper.py"),
                         "--flow", "smokefail", "--task", "smoke 失败注入",
                         "--foreground", "--json",
                         "--budget-ms", str(args.timeout * 1000),
                         timeout=args.timeout * 2 + 60)
            payload = None  # state.json carries the assertion data
            statuses, state = statuses_of(fresh_run_dir(known))
            expect(result.returncode == 20,
                   "exit=%d statuses=%s" % (result.returncode, statuses), key)
            expect(statuses["alpha"] == "failed", statuses, key)
            expect(statuses["bravo"] == "done", statuses, key)
            # The ladder must have run: 2 resumes then the branch failed.
            attempts = state["steps"]["alpha"].get("attempts", {})
            expect(attempts.get("resume") == 2, attempts, key)
            handoffs = sorted(p.name for p in
                              (fresh_run_dir(known) / "handoff").glob("alpha-*.md"))
            expect(handoffs == ["alpha-a1.md", "alpha-a2.md", "alpha-rebuild.md"],
                   handoffs, key)
            report["scenarios"][key] = {"ok": True,
                                        "elapsed_s": round(time.time() - started, 1)}
            close_workspace_of(fresh_run_dir(known), cli)

            # --- scenario 3 (opt-in): gone → rebuild --------------------
            # The worker's pid only shows up in pane process-info (agent
            # list/get carry no pid), so the run is started in the
            # background and killed the moment it is running.
            # A fresh server: earlier scenarios leave cleanup windows and
            # startup races behind them — measured, a third-scenario agent
            # start then times out even with a 120s budget.  Scenario
            # isolation is cheaper than diagnosing the interference.
            if args.kill_gone:
                key = "gone"
                run(binary, "server", "stop")
                server.wait(timeout=5)
                with (root / "server-gone.log").open("w") as log2:
                    server = subprocess.Popen([binary, "server"], env=env,
                                              stdout=log2, stderr=log2)
                for _ in range(100):
                    if (root / "s.sock").exists():
                        break
                    time.sleep(.1)
                # The old socket may linger from the previous server.
                deadline = time.time() + 15
                while time.time() < deadline:
                    if cli("workspace", "list", timeout=15).get("workspaces") is not None:
                        break
                    time.sleep(.2)
                runs_root = project / ".herdr-recipes" / "runs"
                known = set(p.name for p in runs_root.iterdir()) if runs_root.is_dir() else set()
                out_path = root / "gone-out.txt"
                with out_path.open("w") as out:
                    process = subprocess.Popen(
                        ["python3", str(repo / "scripts" / "hsuper.py"),
                         "--flow", "smokegone", "--task", "smoke gone",
                         "--foreground", "--json",
                         "--budget-ms", str(args.timeout * 1000)],
                        env=env, cwd=str(project), stdout=out, stderr=out)

                def gone_run_dir():
                    """The run directory this scenario created."""
                    fresh = sorted(p.name for p in runs_root.iterdir()
                                   if p.name not in known)
                    return runs_root / fresh[-1] if fresh else None

                # Wait until the solo step is running, the agent body is in
                # the foreground AND the prompt has been submitted (the log
                # line): closing the pane before that races the supervisor's
                # own agent start (measured: agent start then fails with
                # agent_pane_not_found and the branch dies).
                deadline = time.time() + 180
                closed = False
                while time.time() < deadline and not closed:
                    run_dir = gone_run_dir()
                    if not run_dir:
                        time.sleep(1)
                        continue
                    state_file = run_dir / "state.json"
                    try:
                        state = json.loads(state_file.read_text())
                    except ValueError:
                        time.sleep(1)
                        continue
                    step = state.get("steps", {}).get("solo") or {}
                    pane_id = step.get("pane_id")
                    if step.get("status") != "running" or not pane_id:
                        time.sleep(1)
                        continue
                    try:
                        step_log = (run_dir / "logs" / "solo.log").read_text()
                    except OSError:
                        step_log = ""
                    if "prompt 已提交" not in step_log and "stalled" not in step_log:
                        time.sleep(1)
                        continue
                    info = cli("pane", "process-info", "--pane", pane_id)
                    proc_info = info.get("process_info") or {}
                    shell_pid = proc_info.get("shell_pid")
                    bodies = [p for p in proc_info.get("foreground_processes", [])
                              if p.get("argv0") == "pi" and p.get("pid") != shell_pid]
                    if bodies:
                        import sys as _s
                        print("CLOSEPOLL pane=%s agent-body=%r" % (pane_id, bodies),
                              file=_s.stderr, flush=True)
                        cli("pane", "close", pane_id)
                        closed = True
                    time.sleep(1)
                expect(closed, "no agent body to close", key)
                # The supervisor should rebuild on a fallback pane; the step
                # and the independent after-step still finish.
                deadline = time.time() + args.timeout
                while time.time() < deadline and process.poll() is None:
                    run_dir = gone_run_dir()
                    if run_dir and (run_dir / "result" / "solo.json").is_file() \
                            and (run_dir / "result" / "after.json").is_file():
                        break
                    time.sleep(2)
                process.wait(timeout=60)
                run_dir = gone_run_dir()
                expect(run_dir is not None, "gone run directory missing", key)
                expect((run_dir / "result" / "solo.json").is_file(),
                       "rebuild did not finish the step", key)
                expect((run_dir / "result" / "after.json").is_file(),
                       "after step did not finish", key)
                expect(process.returncode == 0,
                       "exit=%d" % process.returncode, key)
                state = json.loads((run_dir / "state.json").read_text())
                attempts = state["steps"]["solo"].get("attempts", {})
                expect(attempts.get("rebuild") == 1, attempts, key)
                kinds = [d.get("kind") for d in state.get("deviations", [])]
                expect("restart_new_pane" in kinds, state.get("deviations"), key)
                report["scenarios"][key] = {"ok": True, "closed": True}
                close_workspace_of(run_dir, cli)

            # --- cleanup -------------------------------------------------
            for workspace in cli("workspace", "list")["workspaces"]:
                cli("workspace", "close", workspace["workspace_id"])
            expect(not cli("workspace", "list")["workspaces"], "cleanup left workspaces",
                   "cleanup")
            report["cleaned"] = True
        finally:
            try:
                run(binary, "server", "stop")
            finally:
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.terminate()
                    server.wait(timeout=5)

Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report, ensure_ascii=False, indent=2))
