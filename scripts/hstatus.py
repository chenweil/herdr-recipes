#!/usr/bin/env python3
"""Read one workspace snapshot without changing Herdr or agent state."""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys


class StatusError(RuntimeError):
    """A diagnostic suitable for stderr, without a Python traceback."""


def version():
    try:
        return (Path(__file__).resolve().parents[1] / "VERSION").read_text().splitlines()[0].strip() or "unknown"
    except (OSError, IndexError):
        return "unknown"


def text_value(value):
    return value if isinstance(value, str) and value else None


def query(*args):
    source = "herdr " + " ".join(args)
    try:
        response = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise StatusError(f"{source}: {exc}") from exc
    if response.returncode:
        detail = (response.stderr or response.stdout).strip() or f"exit {response.returncode}"
        raise StatusError(f"{source}: {detail}")
    try:
        payload = json.loads(response.stdout)
    except (ValueError, TypeError) as exc:
        raise StatusError(f"{source}: malformed JSON") from exc
    if not isinstance(payload, dict) or "error" in payload or not isinstance(payload.get("result"), dict):
        raise StatusError(f"{source}: invalid result: {payload}")
    return payload["result"]


def rows(result, key, id_key):
    values = result.get(key)
    if not isinstance(values, list):
        raise StatusError(f"{key}: expected an array")
    seen = set()
    for row in values:
        if not isinstance(row, dict) or not text_value(row.get(id_key)):
            raise StatusError(f"{key}: missing {id_key}")
        if row[id_key] in seen:
            raise StatusError(f"{key}: duplicate {id_key} {row[id_key]}")
        seen.add(row[id_key])
    return values


def pane_order(workspace_id, panes):
    """A tab layout can order a workspace only when it covers every listed pane."""
    fallback = sorted(panes, key=lambda p: p["pane_id"])
    if not panes:
        return fallback, "pane_id"
    try:
        layout = query("pane", "layout", "--pane", fallback[0]["pane_id"]).get("layout")
        if not isinstance(layout, dict) or layout.get("workspace_id") != workspace_id:
            raise StatusError("layout workspace does not match the selected workspace")
        layout_panes = rows(layout, "panes", "pane_id")
        if {p["pane_id"] for p in layout_panes} != {p["pane_id"] for p in panes}:
            raise StatusError("layout does not cover every workspace pane (possibly multiple tabs)")
        coordinates = {}
        for pane in layout_panes:
            rect = pane.get("rect")
            if not isinstance(rect, dict):
                raise StatusError("layout has no pane geometry")
            for axis in ("x", "y"):
                number = rect.get(axis)
                try:
                    finite = not isinstance(number, bool) and isinstance(number, (float, int)) and math.isfinite(number)
                except OverflowError:
                    finite = False
                if not finite:
                    raise StatusError(f"layout has invalid rect.{axis}")
            coordinates[pane["pane_id"]] = (rect["y"], rect["x"], pane["pane_id"])
        return sorted(panes, key=lambda p: coordinates[p["pane_id"]]), "geometry"
    except StatusError as exc:
        print(f"hstatus: warning: {exc}; using exact pane_id order", file=sys.stderr)
        return fallback, "pane_id"


def snapshot(workspace_id=None):
    if workspace_id is None:
        if not os.environ.get("HERDR_PANE_ID"):
            raise StatusError("missing caller context; use --workspace <id> outside Herdr")
        caller = query("pane", "current", "--current").get("pane")
        if not isinstance(caller, dict) or not text_value(caller.get("workspace_id")):
            raise StatusError("pane current: missing caller workspace_id; use --workspace <id>")
        workspace_id = caller["workspace_id"]
    workspaces = rows(query("workspace", "list"), "workspaces", "workspace_id")
    workspace = next((w for w in workspaces if w["workspace_id"] == workspace_id), None)
    if workspace is None:
        raise StatusError(f"workspace not found: {workspace_id}")
    panes = rows(query("pane", "list", "--workspace", workspace_id), "panes", "pane_id")
    panes = [p for p in panes if p.get("workspace_id") in (None, workspace_id)]
    agents = rows(query("agent", "list"), "agents", "pane_id")
    selected = {p["pane_id"] for p in panes}
    agents = {a["pane_id"]: a for a in agents
              if a["pane_id"] in selected and a.get("workspace_id") in (None, workspace_id)}
    panes, order = pane_order(workspace_id, panes)
    summary = []
    for pane in panes:
        agent = agents.get(pane["pane_id"], {})
        summary.append({
            "pane_id": pane["pane_id"],
            "label": text_value(pane.get("label")),
            "kind": (text_value(agent.get("agent")) or text_value(pane.get("agent"))
                     or text_value(agent.get("display_agent")) or text_value(pane.get("display_agent"))),
            "name": text_value(agent.get("name")),
            "status": text_value(agent.get("agent_status")) or text_value(pane.get("agent_status")) or "unknown",
        })
    return {"schema_version": 1,
            "workspace": {"workspace_id": workspace_id, "label": text_value(workspace.get("label"))},
            "pane_order": order, "panes": summary}


def human_output(result):
    def cell(value):
        # Keep labels/statuses containing newlines or terminal escapes on one safe line.
        return "-" if value is None else json.dumps(value, ensure_ascii=False)[1:-1]
    workspace = result["workspace"]
    lines = [f"ws={cell(workspace['workspace_id'])} label={json.dumps(workspace['label'], ensure_ascii=False)} "
             f"panes={len(result['panes'])} order={result['pane_order']}"]
    for pane in result["panes"]:
        lines.append(f"  {cell(pane['pane_id'])} label={json.dumps(pane['label'], ensure_ascii=False)} "
                     f"kind={cell(pane['kind'])} name={cell(pane['name'])} status={cell(pane['status'])}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(prog="hstatus.sh", description=__doc__)
    parser.add_argument("--workspace", metavar="ID", help="inspect this exact workspace; no caller context needed")
    parser.add_argument("--json", action="store_true", help="print a schema_version=1 JSON snapshot")
    parser.add_argument("-v", "-V", "--version", action="version", version=f"hstatus {version()}")
    args = parser.parse_args()
    if args.workspace is not None and not args.workspace.strip():
        parser.error("--workspace requires a nonempty ID")
    try:
        result = snapshot(args.workspace)
    except StatusError as exc:
        print(f"hstatus: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False) if args.json else human_output(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
