"""Opt-in real-model team smoke on a disposable Herdr server and project.

Scenarios: tabs (three workers, standby then delegated report), worktrees (two
parallel changes and checked integration), failure (one requested kind missing).
No user agent/model/permission configuration is rewritten. Requires authenticated
agents; --live-agents explicitly enables their real model calls.
"""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--kind", default="pi")
    parser.add_argument("--scenario", choices=("tabs", "worktrees", "failure"), default="tabs")
    parser.add_argument("--live-agents", action="store_true")
    args = parser.parse_args()
    if not args.live_agents:
        parser.error("this smoke requires explicit --live-agents")
    repo = Path(__file__).resolve().parents[1]
    binary = str(Path(args.binary).resolve())
    report = {"scenario": args.scenario, "server": "isolated", "kind": args.kind, "passed": False}
    output_path = Path(args.output).resolve()
    with tempfile.TemporaryDirectory(prefix="team-workspace-", dir="/private/tmp") as folder:
        root = Path(folder)
        project = root / "project"
        project.mkdir()
        config = root / "herdr.toml"
        config.write_text('[keys]\nprefix="ctrl+b"\n[worktrees]\ndirectory='
                          + json.dumps(str(root / "worktrees")) + '\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HERDR_", "HTEAM_"))}
        isolated = {"HERDR_SOCKET_PATH": str(root / "s.sock"), "HERDR_CONFIG_PATH": str(config),
                    "XDG_CONFIG_HOME": str(root / "config"), "XDG_STATE_HOME": str(root / "state")}
        env.update(isolated)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        # Pin calls from worker shells too; losing inherited env must never hit a user's server.
        wrapper = bin_dir / "herdr"
        wrapper.write_text("#!/bin/sh\n" + "\n".join(
            f"export {key}={shlex.quote(value)}" for key, value in isolated.items())
            + "\nexec " + shlex.quote(binary) + ' "$@"\n')
        wrapper.chmod(0o755)
        env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
        server = None
        coordinator = None

        def run(*items, cwd=project, timeout=30):
            result = subprocess.run(items, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
            if result.returncode:
                raise RuntimeError((items[:3], result.returncode, result.stderr))
            return result.stdout

        def cli(*items):
            text = run(binary, *items)
            return json.loads(text)["result"] if text.strip() else {}

        def wait_for(predicate, label, timeout=240):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                value = predicate()
                if value:
                    return value
                if server.poll() is not None:
                    raise RuntimeError("isolated server exited")
                time.sleep(2)
            raise AssertionError("timeout: " + label)

        def settled():
            agent = cli("agent", "get", coordinator)["agent"]
            return agent.get("agent_status") in ("idle", "done")

        def records_exist():
            records = project / ".herdr-recipes" / "teams"
            return list(records.glob("*/team.md")) and list(records.glob("*/tasks/*.md"))

        def workers():
            return [a for a in cli("agent", "list")["agents"] if a.get("pane_id") != main_pane]

        try:
            if args.scenario == "worktrees":
                run("git", "init", "-q")
                run("git", "config", "user.name", "Herdr smoke")
                run("git", "config", "user.email", "herdr-smoke@example.invalid")
                (project / ".gitignore").write_text(".herdr-recipes/\n")
                (project / "unrelated.txt").write_text("baseline\n")
                run("git", "add", ".gitignore", "unrelated.txt")
                run("git", "commit", "-qm", "smoke baseline")
                base = run("git", "rev-parse", "HEAD").strip()
                (project / "unrelated.txt").write_text("user work must remain\n")
            else:
                # Natural-language 'three' must override this policy without rewriting it.
                (project / ".herdr-team.json").write_text('{"max_workers":2}\n')
                for index in range(1, 4):
                    (project / f"source-{index}.txt").write_text(f"SOURCE-{index}: evidence-{index}\n")

            with (root / "server.log").open("w") as log:
                server = subprocess.Popen([binary, "server"], env=env, cwd=root, stdout=log, stderr=log)
                wait_for(lambda: (root / "s.sock").exists(), "socket", timeout=15)
                foreign = cli("workspace", "create", "--cwd", str(project), "--label", "user-owned", "--focus")
                foreign_ws = foreign["workspace"]["workspace_id"]
                foreign_pane = foreign["root_pane"]["pane_id"]
                launched = run("bash", str(repo / "scripts/hteam.sh"), "-C", str(project),
                               "-k", args.kind, "--no-focus", timeout=90)
                fields = dict(part.split("=", 1) for part in launched.strip().split())
                coordinator = fields["coordinator"]
                team_ws = fields["ws"]
                main_pane = fields["panes"]
                wait_for(settled, "initial idle", timeout=90)
                print("COORDINATOR_READY", team_ws, main_pane, flush=True)
                unassigned = cli("tab", "create", "--workspace", team_ws, "--cwd", str(project),
                                 "--label", "user-unassigned", "--no-focus")
                unassigned_pane = unassigned["root_pane"]["pane_id"]
                protected_before = cli("pane", "layout", "--pane", unassigned_pane)["layout"]

                if args.scenario == "tabs":
                    cli("agent", "prompt", coordinator, "准备三个研究 worker，先只说明角色并待命，"
                        "不要开展研究任务。允许写本地执行记录，其它项目文件保持原样。")
                    wait_for(lambda: len(workers()) == 3 and settled()
                             and all(a.get("agent_status") in ("idle", "done") for a in workers())
                             and records_exist(), "three standby workers and records")
                    print("STANDBY_READY", flush=True)
                    pane_map = {p["pane_id"]: p for p in cli("pane", "list", "--workspace", team_ws)["panes"]}
                    main_tab = pane_map[main_pane]["tab_id"]
                    assert all(pane_map[a["pane_id"]]["tab_id"] != main_tab for a in workers())
                    assert all(a["agent"] == args.kind for a in workers())
                    worker_ids = {a["pane_id"] for a in workers()}
                    cli("agent", "prompt", coordinator, "现在让刚才三个研究者分别检查 source-1.txt、"
                        "source-2.txt、source-3.txt，每人独立把来源与原文写入 note-1.md、note-2.md、"
                        "note-3.md。由你读取三份实际笔记，汇总成 report.md，覆盖三项证据及对应来源。"
                        "不要自己代替研究者编写独立笔记，也不要再增加 worker。")
                    wait_for(lambda: (project / "report.md").exists()
                             and all((project / f"note-{i}.md").exists() for i in range(1, 4))
                             and settled(), "three notes and synthesis")
                    for index in range(1, 4):
                        assert f"evidence-{index}" in (project / f"note-{index}.md").read_text()
                        assert f"evidence-{index}" in (project / "report.md").read_text()
                    assert {a["pane_id"] for a in workers()} == worker_ids
                    assert (project / ".herdr-team.json").read_text() == '{"max_workers":2}\n'
                    report.update(worker_count=3, separate_work_tab=True, reused_workers=True,
                                  explicit_budget_override=True, notes_and_summary=True)
                elif args.scenario == "worktrees":
                    request = (f"完成两个独立并行修改，安排两个 worker：一人新增 alpha.txt，内容 alpha-ready；"
                               f"另一人新增 beta.txt，内容 beta-ready。Git 基线为 {base}。unrelated.txt 的"
                               "未提交内容与任务无关，必须原样保留，主目录 HEAD 也保持不变。交付独立的整合版本，"
                               "检查两份文件内容，写 integration-report.md 到主目录，列出整合 checkout 的"
                               "绝对路径、commit 和实际验证命令/结果。允许必要的本地提交，不推送，不删除现场。")
                    cli("agent", "prompt", coordinator, request)
                    print("WORKTREE_TASK_SENT", flush=True)
                    wait_for(lambda: (project / "integration-report.md").exists() and settled(),
                             "integration report", timeout=420)
                    assert len(workers()) == 2 and all(a["agent"] == args.kind for a in workers())
                    checkout_paths = [Path(line[9:]) for line in run("git", "worktree", "list", "--porcelain").splitlines()
                                      if line.startswith("worktree ")]
                    assert len(checkout_paths) >= 3, checkout_paths
                    integrated = [p for p in checkout_paths if p != project
                                  and (p / "alpha.txt").exists() and (p / "beta.txt").exists()]
                    assert integrated, "no checkout contains both changes"
                    combined = integrated[0]
                    assert (combined / "alpha.txt").read_text().strip() == "alpha-ready"
                    assert (combined / "beta.txt").read_text().strip() == "beta-ready"
                    assert run("git", "show", "HEAD:alpha.txt", cwd=combined).strip() == "alpha-ready"
                    assert run("git", "show", "HEAD:beta.txt", cwd=combined).strip() == "beta-ready"
                    assert run("git", "ls-tree", "-r", "--name-only", "HEAD", ".herdr-recipes", cwd=combined) == ""
                    assert run("git", "rev-parse", "HEAD").strip() == base
                    assert (project / "unrelated.txt").read_text() == "user work must remain\n"
                    report.update(worker_count=2, checkouts=len(checkout_paths), integrated_files=True,
                                  original_head_preserved=True, dirty_file_preserved=True,
                                  integration_commit=run("git", "rev-parse", "HEAD", cwd=combined).strip())
                else:
                    cli("agent", "prompt", coordinator,
                        f"这次安排两个研究 worker：一个 {args.kind}，一个 kind 为 nonexistent-smoke-kind。"
                        "各自只检查 source-1.txt，把结果写到自己的独立笔记。类型不可用时不允许替换，"
                        "可执行的独立工作继续。最后将实际成功人数、失败诊断和未完成影响写到 failure-report.md。")
                    wait_for(lambda: (project / "failure-report.md").exists() and settled(), "partial failure report")
                    assert len(workers()) == 1 and workers()[0]["agent"] == args.kind
                    failure_text = (project / "failure-report.md").read_text()
                    assert "nonexistent-smoke-kind" in failure_text
                    notes = [p for p in project.rglob("*.md") if p.name != "failure-report.md"
                             and ".herdr-recipes" not in p.relative_to(project).parts]
                    report["independent_notes"] = [str(p.relative_to(project)) for p in notes]
                    assert any("evidence-1" in p.read_text() for p in notes), "independent worker evidence missing"
                    report.update(negative_control="unsupported requested kind", no_silent_substitution=True,
                                  worker_count=1, partial_failure_report=True)

                assert records_exist(), "execution notes missing"
                team_notes = "\n".join(p.read_text() for p in
                                       (project / ".herdr-recipes/teams").rglob("*") if p.is_file())
                assert all(a["pane_id"] in team_notes for a in workers()), "worker membership mapping missing"
                assert cli("pane", "layout", "--pane", unassigned_pane)["layout"] == protected_before
                assert cli("pane", "get", foreign_pane)["pane"]["pane_id"] == foreign_pane
                assert cli("pane", "get", main_pane)["pane"]["pane_id"] == main_pane
                workspace_state = cli("workspace", "list")["workspaces"]
                assert next(w for w in workspace_state if w["workspace_id"] == foreign_ws)["focused"]
                report.update(execution_records=True, foreign_resources_preserved=True,
                              caller_preserved=True, focus_preserved=True, passed=True)
                print(args.scenario.upper() + "_OK", flush=True)
        finally:
            if server is not None and server.poll() is None:
                if not report["passed"] and coordinator:
                    try:
                        report["coordinator_output"] = run(binary, "agent", "read", coordinator,
                                                          "--source", "visible", "--format", "text")
                    except Exception as error:
                        report["read_error"] = str(error)
                try:
                    cli("server", "stop")
                finally:
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.terminate()
                        server.wait(timeout=5)
            if (root / "server.log").exists() and not report["passed"]:
                report["server_log_tail"] = (root / "server.log").read_text()[-6000:]
            report["isolated_server_stopped"] = server is None or server.poll() is not None
            output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
