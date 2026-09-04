#!/usr/bin/env python3
"""herdr-pane-switch: switch to the N-th pane (1-based) in the active workspace.

Usage:
    herdr-pane-switch.py [1..6]
    herdr-pane-switch.py -v | -V | --version
"""
import json
import math
import os
import select
import socket
import subprocess
import sys

DEFAULT_SOCKET_PATH = os.path.expanduser("~/.config/herdr/herdr.sock")


class HerdrError(RuntimeError):
    """A user-visible failure while talking to the Herdr CLI or socket."""


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


def sort_panes_by_geometry(panes):
    """Return panes in top-to-bottom, left-to-right reading order."""
    return sorted(panes, key=lambda pane: (pane["rect"]["y"], pane["rect"]["x"]))


def _format_rpc_error(error):
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        if code and message:
            return "%s: %s" % (code, message)
        if message:
            return str(message)
    return "invalid error response"


def _parse_json_response(raw, source):
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HerdrError("%s: malformed JSON: %s" % (source, exc.msg)) from exc

    if not isinstance(response, dict):
        raise HerdrError("%s: response must be a JSON object" % source)
    if "error" in response:
        raise HerdrError("%s: %s" % (source, _format_rpc_error(response["error"])))

    result = response.get("result")
    if not isinstance(result, dict):
        raise HerdrError("%s: response is missing an object result" % source)
    return result


def parse_layout_response(raw):
    """Validate and return the layout object returned by the Herdr CLI."""
    result = _parse_json_response(raw, "herdr pane layout --current")
    layout = result.get("layout")
    if not isinstance(layout, dict):
        raise HerdrError("herdr pane layout --current: response is missing an object layout")
    if not isinstance(layout.get("workspace_id"), str) or not layout["workspace_id"]:
        raise HerdrError("herdr pane layout --current: layout has no workspace_id")

    panes = layout.get("panes")
    if not isinstance(panes, list):
        raise HerdrError("herdr pane layout --current: layout has no panes array")
    for position, pane in enumerate(panes, start=1):
        if not isinstance(pane, dict) or not isinstance(pane.get("pane_id"), str):
            raise HerdrError("herdr pane layout --current: pane %d has no pane_id" % position)
        rect = pane.get("rect")
        if not isinstance(rect, dict):
            raise HerdrError("herdr pane layout --current: pane %s has no rect" % pane["pane_id"])
        for axis in ("x", "y"):
            coordinate = rect.get(axis)
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                raise HerdrError(
                    "herdr pane layout --current: pane %s has invalid rect.%s"
                    % (pane["pane_id"], axis)
                )
            if not math.isfinite(coordinate):
                raise HerdrError(
                    "herdr pane layout --current: pane %s has non-finite rect.%s"
                    % (pane["pane_id"], axis)
                )
    return layout


def parse_socket_response(raw, expected_pane_id=None):
    """Validate a socket response and optionally the pane it reports."""
    result = _parse_json_response(raw, "Herdr pane.focus socket")
    if expected_pane_id is not None:
        pane = result.get("pane")
        if not isinstance(pane, dict) or not isinstance(pane.get("pane_id"), str):
            raise HerdrError("Herdr pane.focus socket: response is missing pane.pane_id")
        if pane["pane_id"] != expected_pane_id:
            raise HerdrError(
                "Herdr pane.focus socket: focused pane %s, expected %s"
                % (pane["pane_id"], expected_pane_id)
            )
    return result


def resolve_socket_path():
    """Use the caller-provided socket before the default local-session socket."""
    return os.environ.get("HERDR_SOCKET_PATH") or DEFAULT_SOCKET_PATH


def socket_rpc(method, params, expected_pane_id=None):
    req = {"jsonrpc": "2.0", "id": "1", "method": method, "params": params}
    msg = json.dumps(req) + "\n"
    path = resolve_socket_path()
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(path)
            s.sendall(msg.encode())
            chunks = []
            while True:
                ready = select.select([s], [], [], 3)[0]
                if not ready:
                    raise HerdrError("Herdr pane.focus socket: timed out waiting for response")
                chunk = s.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
            data = b"".join(chunks).split(b"\n", 1)[0]
        finally:
            s.close()
    except HerdrError:
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise HerdrError("Herdr pane.focus socket %s: %s" % (path, detail)) from exc
    try:
        raw = data.decode()
    except UnicodeDecodeError as exc:
        raise HerdrError("Herdr pane.focus socket: response is not UTF-8") from exc
    if not raw.strip():
        raise HerdrError("Herdr pane.focus socket: response was empty")
    return parse_socket_response(raw, expected_pane_id=expected_pane_id)


def run_layout_command():
    """Fetch the layout for the pane that invoked this script."""
    command = ["herdr", "pane", "layout", "--current"]
    try:
        response = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except FileNotFoundError as exc:
        raise HerdrError("herdr pane layout --current: herdr command not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise HerdrError("herdr pane layout --current: command timed out") from exc

    if response.returncode != 0:
        detail = (response.stderr or response.stdout or "").strip()
        try:
            error_response = json.loads(detail)
        except json.JSONDecodeError:
            error_response = None
        if isinstance(error_response, dict) and "error" in error_response:
            detail = _format_rpc_error(error_response["error"])
        if not detail:
            detail = "exit status %d" % response.returncode
        raise HerdrError("herdr pane layout --current failed: %s" % detail)
    return parse_layout_response(response.stdout)


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-v", "-V", "--version"):
        print("herdr-pane-switch %s" % read_version())
        return

    if len(args) > 1:
        raise HerdrError("usage: herdr-pane-switch.py [1..6]")
    try:
        idx = int(args[0]) if args else 1
    except ValueError as exc:
        raise HerdrError("pane index must be an integer") from exc

    layout = run_layout_command()
    ws_panes = sort_panes_by_geometry(layout["panes"])

    if 1 <= idx <= len(ws_panes):
        target_id = ws_panes[idx - 1]["pane_id"]
        socket_rpc("pane.focus", {"pane_id": target_id}, expected_pane_id=target_id)
    else:
        raise HerdrError(
            "pane %d out of range (workspace has %d panes)" % (idx, len(ws_panes))
        )

if __name__ == "__main__":
    try:
        main()
    except HerdrError as exc:
        print("herdr-pane-switch: %s" % exc, file=sys.stderr)
        sys.exit(1)
