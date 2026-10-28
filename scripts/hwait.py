#!/usr/bin/env python3
"""Wait for a delegated agent to finish, without mistaking a dead agent for a busy one.

Two signals are combined on purpose:

* The agent lifecycle is authoritative. ``pane wait-output`` cannot see an agent
  exit, so a marker-only wait turns a crashed worker into a full-timeout blind
  wait; ``agent wait`` reports the same death within seconds.
* The completion marker stays a cheap accelerator. Herdr searches the pane
  snapshot before it polls, so a marker that is already on screen is matched in
  milliseconds.

Every wait is sliced, so a missing marker can never hide a failure for longer
than one slice.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


EXIT_MARKER = 0
EXIT_ERROR = 1
EXIT_SETTLED = 10
EXIT_BLOCKED = 11
EXIT_GONE = 12
EXIT_TIMEOUT = 13
EXIT_UNKNOWN = 14

VERDICT_EXIT = {
    "marker": EXIT_MARKER,
    "settled": EXIT_SETTLED,
    "blocked": EXIT_BLOCKED,
    "gone": EXIT_GONE,
    "timeout": EXIT_TIMEOUT,
    "unknown": EXIT_UNKNOWN,
}

VERDICT_NEXT = {
    "marker": "agent read 取全文，再按验收标准核对文件/diff/验证结果",
    "settled": "agent read 判定是失败还是已交付但缺标记，缺标记就补记",
    "blocked": "停下报告用户，不要自行回答授权或提问界面",
    "gone": "确认子 agent 已死，按恢复规则重建同角色 worker",
    "unknown": "读输出判定仍在推进还是已崩，仍在推进就继续等",
    "timeout": "读现场判定停滞/失败/仍在跑，不把超时当成功也不当失败",
}

DEFAULT_TIMEOUT_MS = 900000
DEFAULT_SLICE_MS = 60000
DEFAULT_MARKER_TIMEOUT_MS = 1500


class WaitError(RuntimeError):
    """A diagnostic suitable for stderr, without a Python traceback."""


def version():
    try:
        return (Path(__file__).resolve().parents[1] / "VERSION").read_text().splitlines()[0].strip() or "unknown"
    except (OSError, IndexError):
        return "unknown"


def herdr(args, timeout):
    """Run one herdr command and return (returncode, payload)."""
    try:
        response = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise WaitError(f"herdr {' '.join(args)}: {exc}") from exc
    payload = None
    for blob in (response.stdout, response.stderr):
        blob = (blob or "").strip()
        if not blob:
            continue
        try:
            payload = json.loads(blob)
        except ValueError:
            continue
        break
    return response.returncode, payload


def error_code(payload):
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return None


def missing_target(code):
    """True when herdr says the agent or pane identity no longer exists."""
    return isinstance(code, str) and ("not_found" in code or code == "no_agent")


def marker_check(pane_id, marker, timeout_ms):
    """Look for the completion marker.

    Returns (hit, gone, matched_line). A miss is cheap: herdr scans the
    existing snapshot first and only then polls.
    """
    code, payload = herdr(
        ["pane", "wait-output", pane_id, "--match", marker, "--source", "recent",
         "--timeout", str(timeout_ms)],
        timeout=timeout_ms / 1000 + 15,
    )
    if code == 0:
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("matched_line"), str):
            raise WaitError(f"pane wait-output {pane_id}: invalid JSON result")
        return True, False, result["matched_line"]
    if payload is None:
        raise WaitError(f"pane wait-output {pane_id}: invalid JSON (exit {code})")
    failure = error_code(payload)
    if missing_target(failure):
        return False, True, None
    if failure in (None, "timeout"):
        return False, False, None
    raise WaitError(f"pane wait-output {pane_id}: {failure}")


def wait_state(target, slice_ms):
    """Block until the agent changes state or the slice ends.

    Returns (kind, state) with kind in settled|gone|working|error.
    """
    code, payload = herdr(
        ["agent", "wait", target,
         "--until", "idle", "--until", "blocked", "--until", "done", "--until", "unknown",
         "--timeout", str(slice_ms)],
        timeout=slice_ms / 1000 + 20,
    )
    if code == 0:
        result = payload.get("result") if isinstance(payload, dict) else None
        agent = result.get("agent") if isinstance(result, dict) else None
        state = agent.get("agent_status") if isinstance(agent, dict) else None
        if not isinstance(state, str):
            raise WaitError("agent wait: invalid JSON result")
        return "settled", state
    if payload is None:
        raise WaitError(f"agent wait {target}: invalid JSON (exit {code})")
    failure = error_code(payload)
    if missing_target(failure):
        return "gone", None
    if failure == "timeout":
        return "working", None
    return "error", failure or "unknown"


def resolve_pane(target):
    """Return (pane_id, gone). The agent identity is the handle that dies first."""
    code, payload = herdr(["agent", "get", target], timeout=20)
    if code != 0:
        failure = error_code(payload)
        if missing_target(failure):
            return None, True
        raise WaitError(f"agent get {target}: {failure or 'failed'}")
    result = payload.get("result") if isinstance(payload, dict) else None
    agent = result.get("agent") if isinstance(result, dict) else None
    pane_id = agent.get("pane_id") if isinstance(agent, dict) else None
    if not isinstance(pane_id, str) or not pane_id:
        raise WaitError(f"agent get {target}: missing pane_id")
    return pane_id, False


def wait_for(args):
    """Run the sliced dual-signal wait and return the verdict payload."""
    started = time.monotonic()
    target = args.agent or args.pane
    pane_id = args.pane
    if args.agent is not None:
        resolved_pane, gone = resolve_pane(args.agent)
        if gone:
            return {"verdict": "gone", "target": target, "pane_id": None, "state": None,
                    "matched_line": None, "elapsed_ms": int((time.monotonic() - started) * 1000)}
        if pane_id is None:
            pane_id = resolved_pane
        elif pane_id != resolved_pane:
            raise WaitError(f"agent {args.agent} is in {resolved_pane}, not {pane_id}")
    deadline = started + args.timeout / 1000

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"verdict": "timeout", "target": target, "pane_id": pane_id, "state": None,
                    "matched_line": None, "elapsed_ms": int((time.monotonic() - started) * 1000)}
        remaining_ms = max(1, int(remaining * 1000))
        hit, gone, line = marker_check(pane_id, args.marker,
                                       min(args.marker_timeout, remaining_ms))
        if hit:
            return {"verdict": "marker", "target": target, "pane_id": pane_id, "state": None,
                    "matched_line": line, "elapsed_ms": int((time.monotonic() - started) * 1000)}
        if gone:
            return {"verdict": "gone", "target": target, "pane_id": pane_id, "state": None,
                    "matched_line": None, "elapsed_ms": int((time.monotonic() - started) * 1000)}

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"verdict": "timeout", "target": target, "pane_id": pane_id, "state": None,
                    "matched_line": None, "elapsed_ms": int((time.monotonic() - started) * 1000)}

        slice_ms = min(args.slice, max(1, int(remaining * 1000)))
        kind, state = wait_state(target, slice_ms)
        if kind == "gone":
            return {"verdict": "gone", "target": target, "pane_id": pane_id, "state": state,
                    "matched_line": None, "elapsed_ms": int((time.monotonic() - started) * 1000)}
        if kind == "error":
            raise WaitError(f"agent wait {target}: {state}")
        if kind == "settled":
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"verdict": "timeout", "target": target, "pane_id": pane_id, "state": state,
                        "matched_line": None, "elapsed_ms": int((time.monotonic() - started) * 1000)}
            hit, gone, line = marker_check(pane_id, args.marker,
                                           min(args.marker_timeout, max(1, int(remaining * 1000))))
            if hit:
                return {"verdict": "marker", "target": target, "pane_id": pane_id, "state": state,
                        "matched_line": line, "elapsed_ms": int((time.monotonic() - started) * 1000)}
            if gone:
                return {"verdict": "gone", "target": target, "pane_id": pane_id, "state": state,
                        "matched_line": None, "elapsed_ms": int((time.monotonic() - started) * 1000)}
            verdict = "blocked" if state == "blocked" else "unknown" if state == "unknown" else "settled"
            return {"verdict": verdict, "target": target, "pane_id": pane_id, "state": state,
                    "matched_line": None, "elapsed_ms": int((time.monotonic() - started) * 1000)}
        # kind == "working": the slice expired with the agent still busy.


def human_output(result):
    lines = [f"hwait: verdict={result['verdict']} target={result['target']} "
             f"pane={result['pane_id'] or '-'} state={result['state'] or '-'} "
             f"elapsed={result['elapsed_ms'] / 1000:.1f}s"]
    if result.get("matched_line"):
        lines.append(f"  matched: {result['matched_line'][:120]}")
    lines.append(f"  next: {VERDICT_NEXT[result['verdict']]}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(prog="hwait.sh", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", metavar="NAME", help="delegated agent name (preferred identity handle)")
    parser.add_argument("--pane", metavar="ID", help="pane hosting the delegated agent; resolved from --agent when omitted")
    parser.add_argument("--marker", metavar="TEXT", required=True, help="completion marker to look for, e.g. 'DONE T08'")
    parser.add_argument("--timeout", metavar="MS", type=int, default=DEFAULT_TIMEOUT_MS,
                        help=f"overall wait budget in ms (default {DEFAULT_TIMEOUT_MS})")
    parser.add_argument("--slice", metavar="MS", type=int, default=DEFAULT_SLICE_MS,
                        help=f"lifecycle slice in ms (default {DEFAULT_SLICE_MS}, max 60000)")
    parser.add_argument("--marker-timeout", metavar="MS", type=int, default=DEFAULT_MARKER_TIMEOUT_MS,
                        help=f"per-check marker timeout in ms (default {DEFAULT_MARKER_TIMEOUT_MS})")
    parser.add_argument("--json", action="store_true", help="print a schema_version=1 JSON verdict")
    parser.add_argument("-v", "-V", "--version", action="version", version=f"hwait {version()}")
    args = parser.parse_args()
    if not args.agent and not args.pane:
        parser.error("--agent or --pane is required")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.slice <= 0 or args.slice > 60000:
        parser.error("--slice must be positive and at most 60000")
    if args.marker_timeout <= 0:
        parser.error("--marker-timeout must be positive")
    try:
        result = wait_for(args)
    except WaitError as exc:
        print(f"hwait: {exc}", file=sys.stderr)
        return EXIT_ERROR
    payload = {"schema_version": 1, "exit_code": VERDICT_EXIT[result["verdict"]],
               "next": VERDICT_NEXT[result["verdict"]], "marker": args.marker, **result}
    print(json.dumps(payload, ensure_ascii=False) if args.json else human_output(result))
    return VERDICT_EXIT[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
