#!/usr/bin/env python3
"""herdr-pane-switch: switch to the N-th pane (1-based) in the active workspace.

Usage:
    herdr-pane-switch.py [1..6]
    herdr-pane-switch.py -v | -V | --version
"""
import json, os, socket, select, subprocess, sys

SOCK = os.path.expanduser("~/.config/herdr/herdr.sock")


def read_version():
    """从仓库根的 VERSION 文件读版本号。

    跟 scripts/version.sh 共用同一个文件，发版只改一处。
    读不到时返回 "unknown"——版本号只用于显示，缺失不该让脚本挂掉。
    安装后 scripts/ 是 symlink，realpath 能解析到仓库真实路径。
    """
    here = os.path.dirname(os.path.realpath(__file__))
    try:
        with open(os.path.join(here, os.pardir, "VERSION")) as f:
            return f.readline().strip() or "unknown"
    except OSError:
        return "unknown"

def socket_rpc(method, params):
    req = {"jsonrpc": "2.0", "id": "1", "method": method, "params": params}
    msg = json.dumps(req) + "\n"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.sendall(msg.encode())
    ready = select.select([s], [], [], 3)[0]
    data = s.recv(65536) if ready else b"{}"
    s.close()
    for line in data.decode().strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if "result" in obj:
                return obj["result"]
        except json.JSONDecodeError:
            continue
    return {}

def main():
    args = sys.argv[1:]
    if args and args[0] in ("-v", "-V", "--version"):
        print("herdr-pane-switch %s" % read_version())
        return
    idx = int(args[0]) if args else 1

    # 1. List all panes via CLI
    r = subprocess.run(["herdr", "pane", "list"], capture_output=True, text=True, timeout=5)
    data = json.loads(r.stdout)
    panes = data["result"]["panes"]

    # 2. Find focused pane → workspace
    focused = next((p for p in panes if p.get("focused")), None)
    if not focused:
        print("no focused pane", file=sys.stderr)
        sys.exit(1)
    ws_id = focused["workspace_id"]

    # 3. Filter & sort panes in this workspace
    ws_panes = sorted(
        [p for p in panes if p["workspace_id"] == ws_id],
        key=lambda p: p["pane_id"]
    )

    if 1 <= idx <= len(ws_panes):
        target_id = ws_panes[idx - 1]["pane_id"]
        socket_rpc("pane.focus", {"pane_id": target_id})
        # print("Switched to %s (%s)" % (target_id, ws_panes[idx-1].get("cwd","")))
    else:
        print("pane %d out of range (workspace has %d panes)" % (idx, len(ws_panes)),
              file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
