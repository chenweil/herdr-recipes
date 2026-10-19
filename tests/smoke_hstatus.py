"""Manual read-only hstatus smoke on a temporary isolated Herdr server.

Run with --binary /path/to/herdr --output /tmp/hstatus-smoke.json.
Setup creates plain-shell workspaces; it does not start model agents.
"""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--binary", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
binary = str(Path(args.binary).resolve())
repo = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory(prefix="hs-smoke-", dir="/tmp") as folder:
    root = Path(folder)
    config = root / "config.toml"
    config.write_text('[keys]\nprefix = "ctrl+b"\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
    env.update(HERDR_SOCKET_PATH=str(root / "server.sock"), HERDR_CONFIG_PATH=str(config),
               XDG_CONFIG_HOME=str(root / "config"), XDG_STATE_HOME=str(root / "state"))
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "herdr").symlink_to(binary)
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]

    def command(*items, call_env=None):
        return subprocess.run(items, env=call_env or env, cwd=repo, text=True,
                              capture_output=True, timeout=30)

    def cli(*items):
        result = command(binary, *items)
        assert result.returncode == 0, (items, result.stderr)
        return json.loads(result.stdout)["result"] if result.stdout.strip() else {}

    def status(*items, caller=None, stale_workspace=None):
        call_env = env.copy()
        if caller:
            call_env.update(HERDR_ENV="1", HERDR_PANE_ID=caller, HERDR_WORKSPACE_ID=stale_workspace or "stale")
        return command("bash", str(repo / "scripts" / "hstatus.sh"), *items, call_env=call_env)

    version = command(binary, "--version").stdout.strip()
    with (root / "server.log").open("w+") as log:
        server = subprocess.Popen([binary, "server"], env=env, cwd=repo, stdout=log, stderr=log)
        created = []
        try:
            for _ in range(100):
                if (root / "server.sock").exists():
                    break
                if server.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read())
                time.sleep(.1)
            else:
                raise RuntimeError("server socket did not appear")
            before = {w["workspace_id"] for w in cli("workspace", "list")["workspaces"]}
            a = cli("workspace", "create", "--label", "status-demo", "--no-focus")
            ws = a["workspace"]["workspace_id"]
            created.append(ws)
            pane = a["root_pane"]["pane_id"]
            right = cli("pane", "split", pane, "--direction", "right", "--no-focus")["pane"]["pane_id"]
            bottom = cli("pane", "split", pane, "--direction", "down", "--no-focus")["pane"]["pane_id"]
            for pid, label in ((pane, "实现"), (right, "Review"), (bottom, "Shell")):
                cli("pane", "rename", pid, label)
            b = cli("workspace", "create", "--label", "other-workspace", "--no-focus")
            other = b["workspace"]["workspace_id"]
            created.append(other)
            cli("workspace", "focus", other)

            def focus():
                return {w["workspace_id"]: {
                    "focused": w["focused"], "active_tab_id": w.get("active_tab_id"),
                    "panes": {p["pane_id"]: p["focused"] for p in
                              cli("pane", "list", "--workspace", w["workspace_id"])["panes"]},
                } for w in cli("workspace", "list")["workspaces"]}

            focused_before = focus()
            config_before = config.read_bytes()
            explicit = status("--workspace", ws, "--json")
            assert explicit.returncode == 0 and not explicit.stderr, explicit.stderr
            payload = json.loads(explicit.stdout)
            assert payload["schema_version"] == 1 and payload["pane_order"] == "geometry"
            assert payload["workspace"] == {"workspace_id": ws, "label": "status-demo"}
            assert [p["pane_id"] for p in payload["panes"]] == [pane, right, bottom]
            assert [p["label"] for p in payload["panes"]] == ["实现", "Review", "Shell"]
            assert all(p["kind"] is None and p["name"] is None and p["status"] == "unknown" for p in payload["panes"])
            current = status("--json", caller=pane, stale_workspace=other)
            assert current.returncode == 0 and not current.stderr, current.stderr
            assert json.loads(current.stdout) == payload
            # Execute the public entry from a real shell pane so Herdr injects caller context.
            caller_json, caller_stderr, caller_exit = (root / name for name in
                                                      ("caller.json", "caller.stderr", "caller.exit"))
            # Login-shell startup may prepend the system Herdr; keep the tested client/server pair.
            launch = (shlex.join(["env", "PATH=" + env["PATH"], "bash",
                                 str(repo / "scripts" / "hstatus.sh"), "--json"])
                      + " > " + shlex.quote(str(caller_json)) + " 2> " + shlex.quote(str(caller_stderr))
                      + "; printf '%s\\n' \"$?\" > " + shlex.quote(str(caller_exit)))
            cli("pane", "run", pane, launch)
            for _ in range(150):
                if caller_exit.exists() and caller_exit.read_text().strip():
                    break
                time.sleep(.1)
            else:
                raise RuntimeError("real caller entry did not finish")
            assert caller_exit.read_text().strip() == "0", caller_stderr.read_text()
            assert not caller_stderr.read_text(), caller_stderr.read_text()
            assert json.loads(caller_json.read_text()) == payload
            human = status("--workspace", ws)
            assert human.returncode == 0 and len(human.stdout.splitlines()) == 4
            assert "实现" in human.stdout and "kind=- name=- status=unknown" in human.stdout
            missing = status("--json")
            assert missing.returncode == 1 and not missing.stdout and "--workspace" in missing.stderr
            assert focus() == focused_before and config.read_bytes() == config_before

            cli("tab", "create", "--workspace", ws, "--no-focus")
            all_panes = cli("pane", "list", "--workspace", ws)["panes"]
            multitab_focus = focus()
            multitab = status("--workspace", ws, "--json")
            assert multitab.returncode == 0 and "using exact pane_id order" in multitab.stderr
            multiple = json.loads(multitab.stdout)
            assert multiple["pane_order"] == "pane_id"
            assert [p["pane_id"] for p in multiple["panes"]] == sorted(p["pane_id"] for p in all_panes)
            assert focus() == multitab_focus and config.read_bytes() == config_before
            report = {"version": version, "explicit": payload, "caller_matches_explicit": True,
                      "real_pane_entry_verified": True,
                      "human": human.stdout, "multitab": multiple,
                      "focus_and_config_unchanged": True}
            for workspace in created:
                cli("workspace", "close", workspace)
            created.clear()
            assert {w["workspace_id"] for w in cli("workspace", "list")["workspaces"]} == before
            report["workspaces_cleaned"] = True
        finally:
            try:
                for workspace in created:
                    cli("workspace", "close", workspace)
                cli("server", "stop")
            finally:
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.terminate()
                    server.wait(timeout=5)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(version, "explicit/caller/human/multitab OK; focus/config unchanged; resources cleaned")
