#!/usr/bin/env python3
"""hsuper — supervised dispatch: run a declared flow with deterministic bounded recovery.

The recovery ladder implemented here is the one already written in
``scripts/coordinator-prompt.md`` §5 (2 original-session resumes, then 1 rebuild,
then the branch is marked failed).  The difference is the executor: this is a
script, so it is not affected by context compaction, fatigue, or busyness.

Scope (see docs/adr/0004-supervised-dispatch-boundary.md):
  * one run, inside one Herdr session, launched into a dedicated supervisor pane
  * a flat directed ``next`` chain — not a DAG expression language
  * state written per run so ``--resume`` can continue after a restart
  * no background daemon, no task queue, no cross-session auto-resume, no push
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(SCRIPTS_DIR))
import hwait  # noqa: E402  (sibling script; import is guarded by its __main__ check)

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BRANCH_FAILED = 20
EXIT_BRANCH_BLOCKED = 21
EXIT_BUDGET = 22
EXIT_PARTIAL = 23

SCHEMA_VERSION = 1

DEFAULT_KIND = "codex"
DEFAULT_TIMEOUT_MS = 900000
DEFAULT_RETRIES = 2
DEFAULT_REBUILDS = 1
DEFAULT_MAX_PARALLEL = 4

# The 2+1 ladder is policy, not a suggestion. Overriding it needs an explicit
# flag so that silently turning "bounded" into "unbounded" cannot happen by
# editing a flow file.
MAX_SAFE_RETRIES = 2
MAX_SAFE_REBUILDS = 1

STEP_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
ALLOWED_PLACEHOLDERS = ("task", "run_id", "step", "marker", "run_dir")
# One regex for both roles: it captures whatever sits between the braces, so
# a near-miss name such as {{task }} or {{RUN_ID}} is seen and rejected
# instead of silently expanding to nothing.
BRACE_RE = re.compile(r"\{\{([^{}]*)\}\}")

KIND_ALIASES = {"op": "opencode", "cc": "claude", "cd": "codex"}


class FlowError(Exception):
    """A flow definition that cannot be executed as written."""


class UsageError(Exception):
    """Bad arguments or unusable environment."""


def version():
    try:
        return (REPO_ROOT / "VERSION").read_text().splitlines()[0].strip() or "unknown"
    except (OSError, IndexError):
        return "unknown"


# ---------------------------------------------------------------- flow loading

def _tomllib():
    try:
        import tomllib
    except ModuleNotFoundError as exc:  # Python < 3.11
        raise UsageError(
            "hsuper 需要 Python 3.11+ 的 tomllib（当前 %s）。"
            "请升级 python3 后重试。" % platform_version()) from exc
    return tomllib


def platform_version():
    return "%d.%d.%d" % sys.version_info[:3]


def resolve_kind(kind):
    """Resolve the short aliases the repo already uses in hopen-agents.conf."""
    return KIND_ALIASES.get(kind, kind)


def load_flow(path):
    """Read a flow TOML file and return (flow_name, flow_table, source_path)."""
    path = Path(path)
    if not path.is_file():
        raise FlowError("flow 文件不存在: %s" % path)
    tomllib = _tomllib()
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except Exception as exc:
        raise FlowError("flow 文件解析失败 %s: %s" % (path, exc)) from exc

    tables = raw.get("flow")
    if not isinstance(tables, dict) or not tables:
        raise FlowError("flow 文件缺少 [flow.<name>] 段: %s" % path)
    if len(tables) > 1:
        names = ", ".join(sorted(tables))
        raise FlowError("flow 文件包含多个 flow (%s)，一次只允许一个: %s" % (names, path))
    name, table = next(iter(tables.items()))
    if not isinstance(table, dict):
        raise FlowError("[flow.%s] 必须是表: %s" % (name, path))
    return name, table, path


def _step_table(flow):
    steps = flow.get("steps")
    if not isinstance(steps, dict) or not steps:
        raise FlowError("flow 缺少 [flow.<name>.steps]")
    return steps


def validate_flow(flow, flow_name="", allow_unsafe_limits=False, known_kinds=None):
    """Validate a flow table. Returns the list of error strings (empty = valid).

    ``known_kinds`` is the set of agent kinds the local herdr actually
    supports; it is looked up at runtime instead of being hardcoded here.
    """
    errors = []
    steps = flow.get("steps")
    if not isinstance(steps, dict) or not steps:
        return ["flow 缺少 [flow.<name>.steps]"]

    defaults = flow.get("defaults") or {}
    if not isinstance(defaults, dict):
        errors.append("defaults 必须是表")
        defaults = {}

    # 7: step id shape
    for step_id in steps:
        if not isinstance(step_id, str) or not STEP_ID_RE.match(step_id):
            errors.append("步骤 ID 不合法 %r（要求 %s）" % (step_id, STEP_ID_RE.pattern))

    # 1: next targets exist
    for step_id, table in steps.items():
        if not isinstance(table, dict):
            errors.append("步骤 %s 必须是表" % step_id)
            continue
        nxt = table.get("next", [])
        if not isinstance(nxt, list):
            errors.append("步骤 %s 的 next 必须是数组" % step_id)
            continue
        for target in nxt:
            if target not in steps:
                errors.append("步骤 %s 的 next 指向不存在的步骤 %r" % (step_id, target))

    # 4: exactly one of prompt / prompt_file
    for step_id, table in steps.items():
        if not isinstance(table, dict):
            continue
        has_prompt = isinstance(table.get("prompt"), str) and table.get("prompt") != ""
        has_file = isinstance(table.get("prompt_file"), str) and table.get("prompt_file") != ""
        if has_prompt and has_file:
            errors.append("步骤 %s 同时配置了 prompt 和 prompt_file" % step_id)
        elif not has_prompt and not has_file:
            errors.append("步骤 %s 必须且只能配置 prompt 或 prompt_file 之一" % step_id)

    # 5: kind must be a kind herdr actually supports
    for step_id, table in steps.items():
        if not isinstance(table, dict):
            continue
        kind = table.get("kind", defaults.get("kind", DEFAULT_KIND))
        if not isinstance(kind, str) or not kind:
            errors.append("步骤 %s 的 kind 必须是非空字符串" % step_id)
            continue
        resolved = resolve_kind(kind)
        if known_kinds is not None and resolved not in known_kinds:
            errors.append("步骤 %s 的 kind %r 不在 herdr agent start 支持的列表内" % (step_id, kind))

    # 6: recovery limits
    for step_id, table in steps.items():
        if not isinstance(table, dict):
            continue
        for key, limit in (("retries", MAX_SAFE_RETRIES), ("rebuilds", MAX_SAFE_REBUILDS)):
            value = table.get(key, DEFAULT_RETRIES if key == "retries" else DEFAULT_REBUILDS)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append("步骤 %s 的 %s 必须是非负整数" % (step_id, key))
                continue
            if value > limit and not allow_unsafe_limits:
                errors.append(
                    "步骤 %s 的 %s=%d 超过安全上限 %d；如确需放宽请加 --allow-unsafe-limits"
                    % (step_id, key, value, limit))

    # 8: placeholder names — prompt, expect and prompt_file share the same
    # expansion set (flows/_schema.md), so all three are checked here;
    # otherwise a typo inside expect surfaces only at render time, mid-run.
    for step_id, table in steps.items():
        if not isinstance(table, dict):
            continue
        prompt = table.get("prompt")
        if isinstance(prompt, str) and prompt:
            for name in BRACE_RE.findall(prompt):
                if name not in ALLOWED_PLACEHOLDERS:
                    errors.append(
                        "步骤 %s 使用了未知占位符 {{%s}}（允许: %s）"
                        % (step_id, name, ", ".join(ALLOWED_PLACEHOLDERS)))
        expect = table.get("expect", [])
        if isinstance(expect, list):
            for item in expect:
                if isinstance(item, str):
                    for name in BRACE_RE.findall(item):
                        if name not in ALLOWED_PLACEHOLDERS:
                            errors.append(
                                "步骤 %s 的 expect 使用了未知占位符 {{%s}}（允许: %s）"
                                % (step_id, name, ", ".join(ALLOWED_PLACEHOLDERS)))
        prompt_file = table.get("prompt_file")
        if isinstance(prompt_file, str) and prompt_file:
            for name in BRACE_RE.findall(prompt_file):
                if name not in ALLOWED_PLACEHOLDERS:
                    errors.append(
                        "步骤 %s 的 prompt_file 使用了未知占位符 {{%s}}（允许: %s）"
                        % (step_id, name, ", ".join(ALLOWED_PLACEHOLDERS)))

    # numeric sanity for the remaining knobs
    for step_id, table in steps.items():
        if not isinstance(table, dict):
            continue
        timeout = table.get("timeout_ms", DEFAULT_TIMEOUT_MS)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            errors.append("步骤 %s 的 timeout_ms 必须是正整数" % step_id)
        gate = table.get("gate")
        if gate is not None and gate not in ("artifact", "none"):
            errors.append("步骤 %s 的 gate 只能是 artifact 或 none" % step_id)
        for key in ("args", "expect", "next"):
            value = table.get(key)
            if value is not None and not isinstance(value, list):
                errors.append("步骤 %s 的 %s 必须是数组" % (step_id, key))
        for key in ("role", "kind", "model", "cwd", "prompt_file"):
            value = table.get(key)
            if value is not None and not isinstance(value, str):
                errors.append("步骤 %s 的 %s 必须是字符串" % (step_id, key))

    max_parallel = flow.get("max_parallel", DEFAULT_MAX_PARALLEL)
    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel < 0:
        errors.append("flow 的 max_parallel 必须是非负整数（0 = 不限）")

    # 2: cycle detection
    cycle = find_cycle(steps)
    if cycle:
        errors.append("flow: cycle detected: %s" % " -> ".join(cycle))

    # 3: reachability from start
    start = flow.get("start")
    if start is not None:
        if not isinstance(start, list) or not start:
            errors.append("flow 的 start 必须是非空数组")
        else:
            for step_id in start:
                if step_id not in steps:
                    errors.append("flow 的 start 指向不存在的步骤 %r" % step_id)
            reachable = reachable_steps(steps, [s for s in start if s in steps])
            for step_id in steps:
                if step_id not in reachable:
                    errors.append("unreachable step: %s" % step_id)
    else:
        roots = root_steps(steps)
        if not roots:
            errors.append("flow 没有可作为起点的步骤")
        else:
            reachable = reachable_steps(steps, roots)
            for step_id in steps:
                if step_id not in reachable:
                    errors.append("unreachable step: %s" % step_id)

    return errors


def root_steps(steps):
    """The steps that start the flow.

    ``next`` names the *successors* of a step, so a root is a step that no
    other step points at — not a step whose ``next`` list is empty.
    """
    pointed_at = set()
    for table in steps.values():
        if not isinstance(table, dict):
            continue
        for target in table.get("next", []) or []:
            pointed_at.add(target)
    roots = [step_id for step_id in steps if step_id not in pointed_at]
    if roots:
        return roots
    # Every step is pointed at: the graph is a cycle, which rule 2 reports.
    return []


def find_cycle(steps):
    """Return a cycle as a list of step ids, or an empty list."""
    state = {}
    stack = []

    def visit(node):
        state[node] = 1
        stack.append(node)
        table = steps.get(node)
        nxt = table.get("next", []) if isinstance(table, dict) else []
        for target in nxt:
            if target not in steps:
                continue
            if state.get(target) == 1:
                index = stack.index(target)
                return stack[index:] + [target]
            if state.get(target, 0) == 0:
                found = visit(target)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in steps:
        if state.get(node, 0) == 0:
            found = visit(node)
            if found:
                return found
    return []


def reachable_steps(steps, roots):
    seen = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node in seen or node not in steps:
            continue
        seen.add(node)
        table = steps.get(node)
        nxt = table.get("next", []) if isinstance(table, dict) else []
        for target in nxt:
            stack.append(target)
    return seen


def build_plan(flow, flow_name=""):
    """Return (ordered_steps, start_steps, step_defaults).

    The order is a topological order of the ``next`` chain: every step comes
    after the steps it depends on.  A branch whose predecessor failed is still
    present in the order (it is skipped at run time, not dropped from the
    plan).
    """
    steps = _step_table(flow)
    defaults = flow.get("defaults") or {}
    start = flow.get("start")
    if start:
        roots = [s for s in start if s in steps]
    else:
        roots = root_steps(steps)

    # Kahn's algorithm over the ``next`` edges, with the declaration order as
    # the tie-break so the plan is stable between runs.
    order = []
    declared = list(steps)
    pending = {step_id: set() for step_id in steps}
    for step_id, table in steps.items():
        if not isinstance(table, dict):
            continue
        for target in table.get("next", []) or []:
            if target in pending:
                pending[target].add(step_id)

    ready = [s for s in declared if not pending[s]]
    remaining = dict(pending)
    while ready:
        # Declaration order breaks ties, so the plan is reproducible.
        ready.sort(key=declared.index)
        node = ready.pop(0)
        order.append(node)
        for target in steps:
            if node in remaining.get(target, ()):
                remaining[target].discard(node)
                if not remaining[target] and target not in order and target not in ready:
                    ready.append(target)

    # Any step not emitted (a cycle) still gets a slot so validation can
    # report it rather than silently dropping it.
    for node in declared:
        if node not in order:
            order.append(node)
    return order, roots, defaults


def step_settings(flow, step_id, defaults):
    """Merge one step table over the flow defaults into a flat settings dict."""
    steps = _step_table(flow)
    table = steps.get(step_id) or {}
    expect = table.get("expect")
    gate = table.get("gate")
    if gate is None:
        gate = "artifact" if expect else "none"
    return {
        "id": step_id,
        "role": table.get("role", step_id),
        "kind": resolve_kind(table.get("kind", defaults.get("kind", DEFAULT_KIND))),
        "model": table.get("model", defaults.get("model", "")) or "",
        "args": list(table.get("args", defaults.get("args", []) or [])),
        "prompt": table.get("prompt"),
        "prompt_file": table.get("prompt_file"),
        "next": list(table.get("next", []) or []),
        "expect": list(expect or []),
        "gate": gate,
        "timeout_ms": table.get("timeout_ms", DEFAULT_TIMEOUT_MS),
        "retries": table.get("retries", DEFAULT_RETRIES),
        "rebuilds": table.get("rebuilds", DEFAULT_REBUILDS),
        "cwd": table.get("cwd", flow.get("cwd", "")) or "",
    }


# ---------------------------------------------------------------- templates

def render_template(template, context):
    """Expand {{name}} placeholders. Unknown names raise FlowError."""
    def replace(match):
        name = match.group(1)
        if name not in ALLOWED_PLACEHOLDERS:
            raise FlowError("未知占位符 {{%s}}（允许: %s）"
                            % (name, ", ".join(ALLOWED_PLACEHOLDERS)))
        value = context.get(name, "")
        return "" if value is None else str(value)

    rendered = BRACE_RE.sub(replace, template)
    # Any brace expression that survived is a near-miss name: fail loudly.
    leftover = BRACE_RE.findall(rendered)
    if leftover:
        raise FlowError("无法解析的占位符 {{%s}}（允许: %s）"
                        % (leftover[0], ", ".join(ALLOWED_PLACEHOLDERS)))
    return rendered


def marker_for(run_id, step_id):
    """The completion marker. Kept short so a terminal line cannot split it."""
    return "DONE %s-%s" % (run_id, step_id)


def build_prompt(settings, context, run_dir):
    """Return the prompt text for one step.

    The rendered text contains the marker (the worker must print it), but the
    text that is *submitted to the agent* never does — see §9.3.
    """
    template = settings["prompt"]
    if template is None:
        prompt_file = settings.get("prompt_file")
        if not prompt_file:
            raise FlowError("步骤 %s 既没有 prompt 也没有 prompt_file" % settings["id"])
        candidate = Path(prompt_file)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        if not candidate.is_file():
            raise FlowError("步骤 %s 的 prompt_file 不存在: %s" % (settings["id"], candidate))
        template = candidate.read_text()
    local = dict(context)
    local["run_dir"] = str(run_dir)
    local["step"] = settings["id"]
    local["marker"] = context.get("marker", "")
    return render_template(template, local)


def submission_text(run_dir, step_id):
    """The exact text submitted to ``agent prompt``.

    It names the prompt file and nothing else, so the marker string never
    reaches the terminal — that is the whole point of §9.3.
    """
    return "读取 %s 并执行" % (Path(run_dir) / "prompts" / ("%s.md" % step_id))


# ---------------------------------------------------------------- herdr access

class Herdr:
    """A thin, honest wrapper over the herdr CLI.

    Every call goes through the real command line, and every wait carries an
    explicit ``--timeout``.  Pane and agent identities are only ever taken
    from a command's return value — nothing is hardcoded.
    """

    def __init__(self, binary="herdr", timeout=60):
        self.binary = binary
        self.timeout = timeout

    def run(self, *args, timeout=None):
        """Run one herdr command. Returns (returncode, payload)."""
        limit = timeout or self.timeout
        try:
            result = subprocess.run([self.binary, *args], capture_output=True,
                                    text=True, timeout=limit)
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            raise UsageError("herdr %s: %s" % (" ".join(args), exc)) from exc
        payload = None
        for blob in (result.stdout, result.stderr):
            blob = (blob or "").strip()
            if not blob:
                continue
            try:
                payload = json.loads(blob)
            except ValueError:
                continue
            break
        if payload is None:
            raise UsageError("herdr %s: 无效 JSON (exit %d)"
                             % (" ".join(args), result.returncode))
        return result.returncode, payload

    def ok(self, *args, timeout=None):
        """Run a herdr command and require success."""
        code, payload = self.run(*args, timeout=timeout)
        if code != 0:
            raise UsageError("herdr %s 失败: %s"
                             % (" ".join(args), describe_error(payload)))
        return payload

    # -- workspaces / tabs / panes ---------------------------------------

    def create_workspace(self, cwd, label):
        payload = self.ok("workspace", "create", "--cwd", str(cwd),
                          "--label", label, "--no-focus")
        result = payload["result"]
        return (result["workspace"]["workspace_id"],
                result["root_pane"]["pane_id"],
                result["tab"]["tab_id"])

    def create_tab(self, workspace_id, cwd, label):
        payload = self.ok("tab", "create", "--workspace", workspace_id,
                          "--cwd", str(cwd), "--label", label, "--no-focus")
        result = payload["result"]
        return result["tab"]["tab_id"], result["root_pane"]["pane_id"]

    def rename_pane(self, pane_id, label):
        self.ok("pane", "rename", pane_id, label)

    def rename_tab(self, tab_id, label):
        self.ok("tab", "rename", tab_id, label)

    def split_pane(self, parent_pane, direction, ratio=None):
        args = ["pane", "split", parent_pane, "--direction", direction, "--no-focus"]
        if ratio is not None:
            args += ["--ratio", str(ratio)]
        payload = self.ok(*args)
        return payload["result"]["pane"]["pane_id"]

    def list_panes(self, workspace_id):
        payload = self.ok("pane", "list", "--workspace", workspace_id)
        return payload["result"]["panes"]

    def pane_rects(self, pane_id):
        """Return the pane rectangles of the layout containing pane_id."""
        payload = self.ok("pane", "layout", "--pane", pane_id)
        return payload["result"].get("layout")

    def close_pane(self, pane_id):
        self.run("pane", "close", pane_id)

    def close_workspace(self, workspace_id):
        self.run("workspace", "close", workspace_id)

    def close_tab(self, tab_id):
        self.run("tab", "close", tab_id)

    def run_in_pane(self, pane_id, command):
        self.ok("pane", "run", pane_id, command)

    # -- agents -----------------------------------------------------------

    def agent_start(self, name, kind, pane_id, agent_args=None, timeout_ms=120000):
        args = ["agent", "start", name, "--kind", kind, "--pane", pane_id,
                "--timeout", str(timeout_ms)]
        if agent_args:
            args += ["--", *agent_args]
        code, payload = self.run(*args, timeout=timeout_ms / 1000 + 30)
        return code, payload

    def agent_prompt(self, target, text, until=None, timeout_ms=30000):
        # ``agent prompt --timeout`` requires ``--wait`` (measured against
        # herdr 0.9.5: a bare --timeout is rejected with exit 2).
        # - submit (until set): ``--wait --until working --until done``
        #   returns as soon as the submission is observed as activity.  The
        #   default settled match would wait for the whole turn to finish —
        #   measured at 40-60s for a first pi turn — which is the supervisor
        #   loop's job, not the submission's.
        # - resume (until=None): the worker may still be working on the
        #   previous prompt; the dual-signal wait that follows observes
        #   progress, so a plain submission is enough.
        args = ["agent", "prompt", target, text]
        if until:
            args += ["--wait"]
            for state in until:
                args += ["--until", state]
            args += ["--timeout", str(timeout_ms)]
        code, payload = self.run(*args, timeout=timeout_ms / 1000 + 30)
        return code, payload

    def agent_get(self, target):
        code, payload = self.run("agent", "get", target)
        return code, payload

    def agent_send_keys(self, target, *keys):
        return self.run("agent", "send-keys", target, *keys)

    def kill_foreground(self, pane_id, force=False):
        """Kill the foreground processes of a pane, minus the shell itself.

        Measured against herdr 0.9.5: ctrl+c does nothing to an idle pi TUI,
        so stopping a live worker means killing its process.  After a kill
        the pane returns to the shell prompt with the original shell_pid,
        and the agent name is released within a second.
        """
        code, info = self.pane_process_info(pane_id)
        if code != 0 or info is None:
            return False
        killed = False
        for entry in shell_processes(info):
            pid = entry.get("pid")
            if not pid:
                continue
            argv = ["kill"]
            if force:
                argv.append("-9")
            argv.append(str(pid))
            try:
                subprocess.run(argv, capture_output=True, timeout=10)
                killed = True
            except (OSError, subprocess.TimeoutExpired):
                pass
        return killed

    def agent_read(self, target, source="visible", lines=120):
        code, payload = self.run("agent", "read", target, "--source", source,
                                  "--lines", str(lines), "--format", "text")
        return code, payload

    def pane_process_info(self, pane_id):
        code, payload = self.run("pane", "process-info", "--pane", pane_id)
        if code != 0:
            return code, None
        return code, payload.get("result", {}).get("process_info")

    def pane_read(self, pane_id, source="recent-unwrapped", lines=120):
        """Read pane output as plain text (herdr returns text, not JSON)."""
        try:
            result = subprocess.run(
                [self.binary, "pane", "read", pane_id, "--source", source,
                 "--lines", str(lines), "--format", "text"],
                capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            raise UsageError("herdr pane read %s: %s" % (pane_id, exc)) from exc
        return result.stdout or ""

    def agent_kinds(self):
        """Read the kinds the local herdr actually supports.

        The list is taken from ``herdr agent start --help`` at runtime — it is
        not hardcoded here, so a herdr upgrade cannot silently invalidate a
        flow file.
        """
        try:
            result = subprocess.run([self.binary, "agent", "start", "--help"],
                                    capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            return None
        text = (result.stdout or "") + (result.stderr or "")
        match = re.search(r"possible values:\s*(.+)", text)
        if not match:
            return None
        # clap wraps the list in brackets: strip them per item, or the last
        # kind keeps a stray `]` and can never be selected in a flow file.
        items = match.group(1).strip().strip("[]")
        return {item.strip().strip("[]") for item in items.split(",") if item.strip()}

    def notify(self, title, body="", sound="request"):
        self.run("notification", "show", title, "--body", body, "--sound", sound)


def describe_error(payload):
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return "%s: %s" % (error.get("code", "error"), error.get("message", ""))
        if "message" in payload:
            return str(payload["message"])
    return str(payload)


def shell_processes(process_info):
    """The foreground processes of a pane, minus the shell itself."""
    if not process_info:
        return []
    shell_pid = process_info.get("shell_pid")
    result = []
    for entry in process_info.get("foreground_processes", []):
        if shell_pid is not None and entry.get("pid") == shell_pid:
            continue
        result.append(entry)
    return result


def pane_is_idle(process_info):
    """True when the pane sits at its shell prompt with nothing else running."""
    return not shell_processes(process_info)


def pane_shell_ready(process_info):
    """True when the shell itself is up and in the pane's foreground.

    Stronger than ``pane_is_idle``: a freshly split pane may report an empty
    foreground while the shell has not exec'd yet, and starting an agent in
    that window fails with agent_pane_busy (measured).
    """
    if not process_info:
        return False
    shell_pid = process_info.get("shell_pid")
    if shell_pid is None:
        return False
    return any(entry.get("pid") == shell_pid
               for entry in process_info.get("foreground_processes", []))


def agent_argv(settings):
    """Build the native agent arguments for one step.

    ``model`` expands to ``--model <m>``; ``args`` is passed through verbatim
    so a kind-specific flag can always be expressed.
    """
    args = []
    if settings.get("model"):
        args += ["--model", settings["model"]]
    args += list(settings.get("args") or [])
    return args


# ---------------------------------------------------------------- hopen.sh

HOPEN_SH = SCRIPTS_DIR / "hopen.sh"


def hopen_call(script, *args):
    """Run a snippet with ``scripts/hopen.sh`` sourced.

    The layout tables live in ``hopen.sh`` and are the single source of truth
    for this repo — copying them into Python would create a second copy that
    silently drifts, which is exactly the bug class the CHANGELOG records for
    the 21 / 22 / 31 dispatch positions.

    Inside the snippet, ``$1`` is hopen.sh itself and ``$2..`` are the
    arguments passed here.
    """
    if not HOPEN_SH.is_file():
        raise UsageError("缺少 %s" % HOPEN_SH)
    command = ["bash", "-c", script, "_", str(HOPEN_SH)] + [str(a) for a in args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise UsageError("hopen.sh 调用失败: %s" % (result.stderr or "").strip())
    return result.stdout


def layout_plan(code):
    """Return (pane_count, split_plan) for a hopen layout code.

    ``split_plan`` is the list of ``(parent_token, direction, ratio)`` tuples
    that ``hopen.sh`` itself uses, so the worker panes end up in the same
    geometry as a recipe layout.
    """
    out = hopen_call(
        'source "$1"; _panes_for "$2"; echo ---; _steps_for "$2"', code)
    parts = out.split("---", 1)
    if len(parts) != 2:
        raise UsageError("无法解析 hopen.sh 的布局输出")
    try:
        count = int(parts[0].strip())
    except ValueError as exc:
        raise UsageError("无法解析 hopen.sh 的 pane 数: %r" % parts[0].strip()) from exc
    plan = []
    for line in parts[1].splitlines():
        line = line.strip()
        if not line:
            continue
        tokens = line.split()
        parent = tokens[0]
        direction = tokens[1] if len(tokens) > 1 else "right"
        ratio = tokens[2] if len(tokens) > 2 else None
        plan.append((parent, direction, ratio))
    return count, plan


def resolve_kinds(kinds):
    """Resolve kind aliases through ``hopen.sh``'s own ``_resolve_kind``."""
    if not kinds:
        return []
    script = 'source "$1"; for k in "$@"; do _resolve_kind "$k"; done; '
    args = ["bash", "-c", script, "_", str(HOPEN_SH)]
    args.extend(kinds)
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise UsageError("hopen.sh _resolve_kind 失败: %s"
                         % (result.stderr or "").strip())
    resolved = result.stdout.split()
    if len(resolved) != len(kinds):
        raise UsageError("hopen.sh _resolve_kind 输出数量不符")
    return resolved


def layout_for_steps(step_count):
    """Pick a layout code for a number of steps.

    The flow may name a layout explicitly; otherwise the count decides, the
    same way hopen-once.sh picks a layout for an ad-hoc command line.
    """
    if step_count <= 0:
        raise FlowError("流程没有步骤")
    if step_count <= 2:
        return "11"
    if step_count == 3:
        return "12"
    if step_count == 4:
        return "22"
    raise FlowError("步骤数 %d 超出内置布局范围（支持 1-4），请在 flow 里显式写 layout"
                    % step_count)


def position_names(code, count):
    """The visual position name of each created pane index.

    ``hopen.sh``'s own ``_position_for`` is the source of truth, so a pane
    created here carries the same label the recipe layouts use.
    """
    if count <= 0:
        return []
    script = ('source "$1"; for i in $(seq 0 $(( $2 - 1 ))); do '
              '_position_for "$3" "$i"; done')
    args = ["bash", "-c", script, "_", str(HOPEN_SH), str(count), code]
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise UsageError("hopen.sh _position_for 失败: %s"
                         % (result.stderr or "").strip())
    names = result.stdout.split()
    if len(names) != count:
        raise UsageError("hopen.sh _position_for 输出数量不符")
    return names


def visual_order(herdr, panes):
    """Sort panes into visual reading order (top to bottom, left to right).

    The order comes from herdr's own layout rectangles rather than from the
    order the panes happened to be created in.  Creation order differs from
    visual order for the 21 / 22 / 31 layouts, and using creation order is
    the documented root cause of the earlier mis-dispatch bug.
    """
    rects = {}
    for pane in panes:
        pane_id = pane.get("pane_id")
        if not pane_id:
            continue
        code, payload = herdr.run("pane", "layout", "--pane", pane_id)
        if code != 0:
            continue
        layout = (payload.get("result") or {}).get("layout") or {}
        for entry in layout.get("panes", []):
            if entry.get("pane_id") == pane_id:
                rects[pane_id] = entry.get("rect") or {}
                break

    def key(item):
        pane_id = item.get("pane_id")
        rect = rects.get(pane_id) or {}
        return (rect.get("y", 0), rect.get("x", 0))

    return sorted(panes, key=key)



# ---------------------------------------------------------------- state

RUNS_DIRNAME = ".herdr-recipes/runs"


def runs_root(cwd):
    return Path(cwd) / RUNS_DIRNAME


def new_run_id():
    """A short, lowercase, herdr-safe run id."""
    return os.urandom(2).hex()


def run_dir_name(run_id, started_at=None):
    """``<YYYYMMDD>-<HHMM>-<run_id>``.

    The stamp is formatted from the tm fields directly: hex-looking stamps
    have appeared in the wild with strftime (cause unknown, runs were
    otherwise healthy — see herdr-research §11), and a run directory outside
    this pattern breaks ``find_run_dir`` and ``--resume``.  Field formatting
    cannot produce anything but digits.
    """
    stamp = started_at or time.localtime()
    return "%04d%02d%02d-%02d%02d-%s" % (stamp.tm_year, stamp.tm_mon,
                                          stamp.tm_mday, stamp.tm_hour,
                                          stamp.tm_min, run_id)


def make_run_dir(cwd, run_id, started_at=None):
    path = runs_root(cwd) / run_dir_name(run_id, started_at)
    for sub in ("prompts", "result", "logs", "handoff"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def write_json_atomic(path, payload):
    """Write JSON via tmp + os.replace so a kill never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def write_text_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def load_state(path):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise UsageError("无法读取状态文件 %s: %s" % (path, exc)) from exc


class State:
    """The per-run state file.

    Every mutation is written through :func:`write_json_atomic`, so a kill in
    the middle of a step leaves a readable state file behind.  Mutations are
    also serialized with a lock: parallel steps update the same file from
    their own threads, and two read-modify-write passes would otherwise
    clobber each other's fields.
    """

    def __init__(self, path, data):
        self.path = Path(path)
        self.data = data
        self._lock = threading.Lock()

    @classmethod
    def create(cls, path, run_id, flow_name, task, cwd, started_at=None):
        data = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "flow": flow_name,
            "task": task,
            "cwd": str(cwd),
            "started_at": iso_now(),
            "updated_at": iso_now(),
            "workspace_id": None,
            "tab_id": None,
            "supervisor_pane": None,
            "steps": {},
            "deviations": [],
        }
        state = cls(path, data)
        state.save()
        return state

    @classmethod
    def load(cls, path):
        return cls(path, load_state(path))

    def save(self):
        with self._lock:
            self.data["updated_at"] = iso_now()
            write_json_atomic(self.path, self.data)

    def step(self, step_id):
        return self.data.setdefault("steps", {}).setdefault(step_id, {})

    def update_step(self, step_id, **fields):
        entry = self.step(step_id)
        entry.update(fields)
        self.save()
        return entry

    def record_deviation(self, step_id, kind, why):
        deviation = {"step": step_id, "kind": kind, "why": why}
        self.data.setdefault("deviations", []).append(deviation)
        self.save()
        return deviation

    def status(self, step_id):
        return (self.data.get("steps", {}).get(step_id) or {}).get("status", "pending")


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ---------------------------------------------------------------- reporting

def format_report(state, elapsed_s):
    """The human-readable summary printed at the end of a run."""
    steps = state.data.get("steps", {})
    order = [sid for sid in steps]
    lines = []
    lines.append("run %s  flow %s  用时 %s"
                 % (state.data.get("run_id"), state.data.get("flow"),
                    format_duration(elapsed_s)))
    lines.append("─" * 41)
    for step_id in order:
        entry = steps[step_id] or {}
        status = entry.get("status", "pending")
        icon = {"done": "✅", "failed": "❌", "blocked": "⚠️", "running": "⏳",
                "skipped": "⏸", "pending": "·"}.get(status, "·")
        attempts = entry.get("attempts", {}) or {}
        detail = []
        if entry.get("role"):
            detail.append(str(entry["role"]))
        if entry.get("model"):
            detail.append(str(entry["model"]))
        recovery = []
        if attempts.get("resume"):
            recovery.append("恢复%d" % attempts["resume"])
        if attempts.get("rebuild"):
            recovery.append("重建%d" % attempts["rebuild"])
        if entry.get("reason"):
            detail.append(str(entry["reason"]))
        if recovery:
            detail.append(" ".join(recovery))
        lines.append("%-10s %s %-8s %-8s %s"
                     % (step_id, icon, status, entry.get("duration_s") or "—",
                        "  ".join(detail)))
    lines.append("─" * 41)
    lines.append("交付: %s" % state.path.parent)
    return "\n".join(lines)


def format_duration(seconds):
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return "%ds" % seconds
    minutes, rest = divmod(seconds, 60)
    return "%dm%02ds" % (minutes, rest)



# ---------------------------------------------------------------- recovery

# The ladder is the one in coordinator-prompt.md §5: two original-session
# resumes, then one rebuild, then the branch is marked failed.  The numbers are
# policy, so they live in one place and are asserted by the tests.
LADDER_RESUMES = 2
LADDER_REBUILDS = 1

# How many times a marker hit without evidence may re-arm the wait before the
# step is treated as a real failure.  Without a bound, a marker that keeps
# matching (an echo) would re-arm forever and the run would never end.
MAX_REARM = 2
# 连续多少次「仍有推进迹象」的观察后转入恢复梯（等待一直异常/输出恒定的 worker
# 不该无限等下去）。
MAX_STALLS = 3


def classify_verdict(verdict):
    """Map an hwait verdict to the recovery ladder's four positions."""
    return {
        "marker": "advance",
        "settled": "resume",
        "blocked": "blocked",
        "gone": "rebuild",
        "timeout": "resume",
        "unknown": "resume",
    }.get(verdict, "resume")


def ladder_action(resume_count, rebuild_count, verdict, retries, rebuilds):
    """Decide the next action for a step.

    Returns one of ``advance`` / ``resume`` / ``rebuild`` / ``fail`` /
    ``blocked`` / ``continue``.

    The order matters and is the whole point of the ladder:

    * ``gone`` means the session is gone — resuming it is impossible, so it
      goes straight to a rebuild (but a rebuild still counts against the
      rebuild budget).
    * ``settled`` / ``timeout`` / ``unknown`` first try to resume the original
      session, up to ``retries`` times.
    * Once the resumes are used up, one rebuild is allowed.
    * After that the branch fails and the other branches continue.
    """
    if verdict == "marker":
        return "advance"
    if verdict == "blocked":
        return "blocked"

    if verdict == "gone":
        if rebuild_count < rebuilds:
            return "rebuild"
        return "fail"

    if resume_count < retries:
        return "resume"
    if rebuild_count < rebuilds:
        return "rebuild"
    return "fail"


def resume_prompt_text(run_id, step_id, summary):
    """The message sent to a resumed session.

    It points at the prompt file instead of repeating it, and it never
    contains the completion marker — the marker only ever reaches the terminal
    through the worker's own output.
    """
    return (
        "继续任务 %s-%s。已收到你的报错：%s。检查已有改动，从中断点完成剩余工作，"
        "仍按 prompts/%s.md 的约定交付。"
        % (run_id, step_id, summary or "（无摘要）", step_id)
    )


def handoff_text(run_id, step_id, summary, attempts, extra=None):
    """The handoff record written before a rebuild."""
    lines = [
        "# 交接记录 %s-%s" % (run_id, step_id),
        "",
        "- 任务 ID: %s-%s" % (run_id, step_id),
        "- 错误摘要: %s" % (summary or "（无摘要）"),
        "- 已用恢复次数: resume=%d rebuild=%d" % (attempts.get("resume", 0),
                                                 attempts.get("rebuild", 0)),
    ]
    if extra:
        lines.extend(extra)
    lines.extend([
        "",
        "替代 worker 请阅读 prompts/%s.md 的完整约定，保留已有改动，从中断点继续，"
        "并按同一约定交付。" % step_id,
    ])
    return "\n".join(lines) + "\n"


def result_status(run_dir, step_id):
    """Read ``result/<step>.json`` and return (status, parsed_or_None)."""
    path = Path(run_dir) / "result" / ("%s.json" % step_id)
    if not path.is_file():
        return None, None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    status = payload.get("status")
    return (status if isinstance(status, str) else None), payload


def gate_ok(settings, run_dir, cwd):
    """Check the evidence gate for one step.

    Two things must hold before a step is allowed to advance:

    1. every ``expect`` path exists (relative to the step's cwd, or absolute),
    2. ``result/<step>.json`` exists and parses.

    A marker on the screen is only a *candidate*; this gate is what turns it
    into progress.  Without it a pasted prompt could advance the whole flow.
    """
    if settings.get("gate") != "artifact":
        return True
    base = Path(cwd)
    # The rendered list is used when the supervisor has prepared the step;
    # the raw list is the fallback for direct callers.
    expected_paths = settings.get("expect_rendered")
    if expected_paths is None:
        expected_paths = settings.get("expect") or []
    for expected in expected_paths:
        candidate = Path(expected)
        if not candidate.is_absolute():
            candidate = base / candidate
        if candidate.exists():
            continue
        # A glob in `expect` matches when it selects anything.
        if any(char in expected for char in "*?["):
            import glob as _glob
            pattern = str(candidate)
            if _glob.glob(pattern):
                continue
        return False
    status, payload = result_status(run_dir, settings["id"])
    if payload is None:
        # No result file, or one that does not parse: no evidence.
        return False
    # A result that declares a status must declare success.  A worker that
    # wrote "failed" cannot advance the flow just because a marker appeared.
    if isinstance(payload.get("status"), str) and payload["status"] != "done":
        return False
    return True


# ---------------------------------------------------------------- supervisor

class TeeStream:
    """A write-through proxy that mirrors one stream into a log file."""

    def __init__(self, original, mirror):
        self.original = original
        self.mirror = mirror

    def write(self, text):
        try:
            self.original.write(text)
            self.original.flush()
        except (OSError, ValueError):
            pass
        try:
            self.mirror.write(text)
            self.mirror.flush()
        except (OSError, ValueError):
            pass
        return len(text)

    def flush(self):
        try:
            self.original.flush()
        except (OSError, ValueError):
            pass
        try:
            self.mirror.flush()
        except (OSError, ValueError):
            pass

    def close(self):
        try:
            self.mirror.close()
        except (OSError, ValueError):
            pass

    def isatty(self):
        try:
            return self.original.isatty()
        except (OSError, ValueError):
            return False

    def __getattr__(self, name):
        return getattr(self.original, name)


class Supervisor:
    """Runs one flow with deterministic bounded recovery.

    The supervisor is deliberately not a workflow engine: the flow is a flat
    ``next`` chain, one branch failing does not stop the others, and the whole
    thing lives inside one Herdr session.
    """

    def __init__(self, herdr, state, flow, run_dir, options):
        self.herdr = herdr
        self.state = state
        self.flow = flow
        self.run_dir = Path(run_dir)
        self.options = options
        self.run_id = state.data["run_id"]
        self.task = state.data.get("task", "")
        self.cwd = Path(state.data.get("cwd") or options.cwd or os.getcwd())
        self.settings_cache = {}
        self.worker_panes = {}
        self.step_results = {}  # step_id -> terminal status, set by threads
        self.started_at = time.time()
        self.budget_exhausted = False
        self.budget_ms = options.budget_ms or 0
        # 同时存活的 worker 上限：CLI 覆盖 flow，0 = 不限。1 = 严格串行
        # （也是测试确定性需要的路径）。
        override = getattr(options, "max_parallel", None)
        flow_value = self.flow.get("max_parallel", DEFAULT_MAX_PARALLEL)
        self.max_parallel = override if override is not None else flow_value

    # -- helpers ----------------------------------------------------------

    def settings(self, step_id):
        if step_id not in self.settings_cache:
            defaults = self.flow.get("defaults") or {}
            self.settings_cache[step_id] = step_settings(self.flow, step_id, defaults)
        return self.settings_cache[step_id]

    def log(self, step_id, message):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), message)
        print(line, flush=True)
        if step_id:
            path = self.run_dir / "logs" / ("%s.log" % step_id)
            try:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass

    def attach_tee(self, path):
        """Mirror everything printed on stdout into ``logs/supervisor.log``.

        Only used in the supervisor pane (``--in-pane``): the pane is the
        visible surface, the file is the durable record.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            stream = path.open("a", encoding="utf-8")
        except OSError:
            return
        tee = TeeStream(sys.stdout, stream)
        sys.stdout = tee
        if not isinstance(sys.stderr, TeeStream):
            sys.stderr = TeeStream(sys.stderr, stream)

    def detach_tee(self):
        if isinstance(sys.stdout, TeeStream):
            sys.stdout.close()
            sys.stdout = sys.__stdout__
        if isinstance(sys.stderr, TeeStream):
            sys.stderr.close()
            sys.stderr = sys.__stderr__

    def agent_name(self, step_id):
        """A herdr-safe agent name: lowercase, <= 32 chars, unique per run."""
        base = "hsuper-%s-%s" % (self.run_id, step_id)
        cleaned = re.sub(r"[^a-z0-9_-]+", "-", base.lower()).strip("-")
        if not cleaned or not cleaned[0].isalpha():
            cleaned = "h" + cleaned
        return cleaned[:32]

    def worker_name(self, step_id):
        """The agent a step should be talked to.

        A rebuild may have renamed the worker (``...r`` fallback) and the
        rename lands in the state file — interacting with the recomputed
        name instead would target an agent that no longer exists, voiding
        the whole rebuild action.  The recorded name wins.
        """
        recorded = (self.state.data["steps"].get(step_id) or {}).get("agent_name")
        return recorded or self.agent_name(step_id)

    def marker(self, step_id):
        return marker_for(self.run_id, step_id)

    def context_for(self, step_id):
        """The placeholder context of one step."""
        return {
            "task": self.task,
            "run_id": self.run_id,
            "step": step_id,
            "marker": self.marker(step_id),
            "run_dir": str(self.run_dir),
        }

    def render_expect(self, step_id, settings):
        """Expand the placeholders inside ``expect``.

        ``expect`` may point at the run directory (``{{run_dir}}/result/x.json``),
        so it has to be rendered exactly like the prompt is.
        """
        context = self.context_for(step_id)
        rendered = []
        for item in settings.get("expect") or []:
            rendered.append(render_template(item, context))
        return rendered

    # -- resources --------------------------------------------------------

    def ensure_worker_pane(self, step_id):
        """Return the pane for a step.

        The worker panes are created up front, one per step, in the worker
        tab.  The mapping from step to pane follows the *visual* order of the
        layout, not the order the panes were created in — that difference is
        the documented root cause of the 21 / 22 / 31 mis-dispatch bug.
        """
        if step_id in self.worker_panes:
            return self.worker_panes[step_id]
        mapping = self.state.data.get("step_panes") or {}
        pane_id = mapping.get(step_id)
        if not pane_id:
            raise UsageError("步骤 %s 没有对应的 worker pane" % step_id)
        self.worker_panes[step_id] = pane_id
        return pane_id

    # -- dispatch ---------------------------------------------------------

    def write_prompt_file(self, step_id, settings):
        """Render the prompt and write it to disk.

        The marker appears in the file, never in the submitted text.
        """
        marker = self.marker(step_id)
        context = {
            "task": self.task,
            "run_id": self.run_id,
            "step": step_id,
            "marker": marker,
        }
        text = build_prompt(settings, context, self.run_dir)
        path = self.run_dir / "prompts" / ("%s.md" % step_id)
        write_text_atomic(path, text)
        return path, text

    def start_agent(self, step_id, settings, pane_id):
        """Start the worker for a step.

        Returns (ok, diagnostic).  A failed start is not a fatal error: the
        ladder treats it like any other failure and tries to recover.
        """
        name = self.agent_name(step_id)
        args = agent_argv(settings)
        # A freshly split pane may not be at its shell prompt yet; starting
        # an agent there fails with agent_pane_busy (measured).  Wait for
        # the shell to be up before the first start.
        self.wait_for_shell_ready(pane_id, timeout_ms=30000)
        code, payload = self.herdr.agent_start(name, settings["kind"], pane_id,
                                               agent_args=args or None,
                                               timeout_ms=120000)
        if code != 0 and describe_error(payload).startswith("timeout"):
            # Measured: the startup detection itself can time out on a
            # loaded machine (a long pi first-launch) while the pane is
            # still fine.  Wait for the shell and start once more before
            # the ladder treats this as a failure.
            self.wait_for_shell_ready(pane_id, timeout_ms=15000)
            code, payload = self.herdr.agent_start(
                name, settings["kind"], pane_id,
                agent_args=args or None, timeout_ms=120000)
        if code == 0:
            return True, None
        error = describe_error(payload)
        # agent_not_ready means the agent exists but is blocked on startup
        # (an onboarding prompt, for example).  The name is still usable, so
        # treat it as a start that needs a human, not as a hard failure.
        code_name = ""
        if isinstance(payload, dict):
            code_name = (payload.get("error") or {}).get("code", "")
        if code_name == "agent_not_ready":
            return True, error
        return False, error

    def submit(self, step_id, pane_id):
        """Submit the prompt by pointing the agent at the prompt file.

        A stall is not a failure (measured): the submission was written but
        the worker did not show activity within 5s — a slow first turn, or
        a freshly rebuilt agent that is still warming up.  The text may
        still be processed; the dual-signal wait and the recovery ladder
        decide.  Returns (True, diagnostic) for a stalled submission.
        """
        name = self.worker_name(step_id)
        text = submission_text(self.run_dir, step_id)
        # Wait only for the submission to take effect (working observed); the
        # turn itself is waited for by the dual-signal loop.
        code, payload = self.herdr.agent_prompt(
            name, text, until=("working", "done"), timeout_ms=30000)
        if code != 0:
            diagnostic = describe_error(payload)
            if "agent_prompt_stalled" in diagnostic:
                # Measured: an agent that just became ready occasionally
                # swallows the first submission (no activity within 5s,
                # every resume also stalls).  One retry after a short pause
                # fixes it; a duplicated submission is harmless for the
                # idempotent result-file tasks flows use.
                time.sleep(3)
                code, payload = self.herdr.agent_prompt(
                    name, text, until=("working", "done"), timeout_ms=30000)
                if code == 0:
                    return True, None
                diagnostic = describe_error(payload)
            if "agent_prompt_stalled" in diagnostic:
                self.log(step_id, "prompt 提交未观察到活动（stalled），继续等待")
                return True, diagnostic
            return False, diagnostic
        self.log(step_id, "prompt 已提交")
        return True, None

    # -- waiting ----------------------------------------------------------

    def wait_step(self, step_id, settings, pane_id, marker, timeout_ms):
        """Wait for one attempt.  Returns the hwait verdict payload.

        A broken wait (invalid JSON, a mismatched pane) is not a crash: it
        becomes an ``unknown`` verdict so the ladder can decide what to do.
        """
        args = argparse.Namespace(
            agent=self.worker_name(step_id),
            pane=pane_id,
            marker=marker,
            timeout=timeout_ms,
            slice=hwait.DEFAULT_SLICE_MS,
            marker_timeout=hwait.DEFAULT_MARKER_TIMEOUT_MS,
        )
        try:
            return hwait.wait_for(args)
        except hwait.WaitError as exc:
            self.log(step_id, "等待失败，按 unknown 处理: %s" % exc)
            return {"verdict": "unknown", "target": args.agent, "pane_id": pane_id,
                    "state": None, "matched_line": None, "elapsed_ms": 0}

    # -- recovery ---------------------------------------------------------

    def apply_resume(self, step_id, settings, pane_id, attempts, summary):
        """Resume the original session: same agent, same pane, same name.

        Callers increment ``attempts['resume']`` first, so the handoff file
        is numbered a1, a2, ... — one record per resume attempt.
        """
        name = self.worker_name(step_id)
        text = resume_prompt_text(self.run_id, step_id, summary)
        handoff = self.run_dir / "handoff" / ("%s-a%d.md"
                                              % (step_id, attempts.get("resume", 0)))
        write_text_atomic(handoff, handoff_text(
            self.run_id, step_id, summary, attempts))
        # Plain submission: the worker may still be working; the dual-signal
        # wait that follows is what observes progress.
        code, payload = self.herdr.agent_prompt(name, text, timeout_ms=30000)
        if code != 0:
            self.log(step_id, "resume 提交失败: %s" % describe_error(payload))
            return False
        self.log(step_id, "resume #%d 已提交" % attempts.get("resume", 0))
        return True

    def apply_rebuild(self, step_id, settings, pane_id, attempts, summary):
        """Rebuild the worker: same pane, same name, fresh session.

        The measurement in §16.1 matters here: herdr keeps the agent *name*
        reserved for a few seconds after the process is gone, so a rebuild
        that races the release fails with ``agent_name_taken``.  The rebuild
        therefore stops the old session first, waits for the name to be
        released, and — if the pane is not back at a shell prompt — falls
        back to a new pane and records the deviation.
        """
        name = self.worker_name(step_id)
        handoff = self.run_dir / "handoff" / ("%s-rebuild.md" % step_id)
        write_text_atomic(handoff, handoff_text(
            self.run_id, step_id, summary, attempts,
            extra=["- 重建方式: 同 pane 同名", "- 目标 pane: %s" % pane_id]))

        # The old session must be gone before a same-name start can work.
        # Measured: ctrl+c does nothing to an idle pi TUI; killing the
        # worker's process returns the pane to the shell prompt with the
        # original shell_pid, and the name is released within a second.
        code, info = self.herdr.pane_process_info(pane_id)
        if code != 0 or info is None:
            # The pane itself is gone (terminal closed, or a pane close
            # took the worker with it): a same-pane rebuild cannot work.
            new_pane = self.fallback_pane(pane_id, settings)
            self.state.record_deviation(
                step_id, "restart_new_pane",
                "pane gone (pane %s)" % pane_id)
            self.worker_panes[step_id] = new_pane
            pane_id = new_pane
            self.log(step_id, "pane 已消失，回退：改用新 pane %s" % new_pane)
        elif not pane_is_idle(info):
            self.log(step_id, "旧 worker 仍占前台，停止其进程")
            self.herdr.kill_foreground(pane_id)
            time.sleep(2)
            if not self.wait_for_pane_idle(pane_id, timeout_ms=15000):
                self.herdr.kill_foreground(pane_id, force=True)
                time.sleep(1)
                if not self.wait_for_pane_idle(pane_id, timeout_ms=10000):
                    new_pane = self.fallback_pane(pane_id, settings)
                    self.state.record_deviation(
                        step_id, "restart_new_pane",
                        "old pane not at shell prompt (pane %s)" % pane_id)
                    self.worker_panes[step_id] = new_pane
                    pane_id = new_pane
                    self.log(step_id, "回退：改用新 pane %s" % new_pane)

        # The old agent identity must be released before the name can be
        # reused.  Killing the process is what releases it.
        if not self.name_is_free(name):
            self.log(step_id, "等待 agent 名字释放")
            if not self.wait_for_name_free(name, timeout_ms=20000):
                self.log(step_id, "agent 名字 %s 仍被占用，重建改用新名字" % name)
                self.state.record_deviation(
                    step_id, "rebuild_new_name",
                    "agent name %s still reserved" % name)
                name = self.agent_name(step_id) + "r"
                self.state.update_step(step_id, agent_name=name)

        code, payload = self.herdr.agent_start(name, settings["kind"], pane_id,
                                               agent_args=agent_argv(settings) or None,
                                               timeout_ms=120000)
        if code != 0:
            self.log(step_id, "重建失败: %s" % describe_error(payload))
            return False, pane_id
        self.state.update_step(step_id, agent_name=name, pane_id=pane_id)
        self.log(step_id, "重建完成: %s on %s" % (name, pane_id))
        return True, pane_id

    def name_is_free(self, name):
        code, _ = self.herdr.agent_get(name)
        return code != 0

    def wait_for_pane_idle(self, pane_id, timeout_ms=30000):
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            code, info = self.herdr.pane_process_info(pane_id)
            if code != 0 or info is None or pane_is_idle(info):
                return True
            time.sleep(1)
        return False

    def wait_for_shell_ready(self, pane_id, timeout_ms=30000):
        """Wait until the shell itself is in the pane's foreground."""
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            code, info = self.herdr.pane_process_info(pane_id)
            if code == 0 and pane_shell_ready(info):
                return True
            time.sleep(0.5)
        return False

    def wait_for_name_free(self, name, timeout_ms=30000):
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if self.name_is_free(name):
                return True
            time.sleep(1)
        return False

    def fallback_pane(self, dead_pane, settings):
        """A fresh pane for a worker whose own pane cannot host a rebuild.

        Splitting needs a *pane* id, not a tab id; the dead pane's siblings
        come first, then the supervisor pane — a pane the supervisor knows
        is alive because the run is still executing inside it.
        """
        for candidate in [p for p in self.state.data.get("worker_panes", [])
                          if p != dead_pane] + \
                [self.state.data.get("supervisor_pane")]:
            if not candidate:
                continue
            code, info = self.herdr.pane_process_info(candidate)
            if code == 0 and info is not None and pane_shell_ready(info):
                try:
                    new_pane = self.herdr.split_pane(candidate, "right")
                    self.herdr.rename_pane(new_pane, settings["role"])
                    return new_pane
                except UsageError as exc:
                    self.log(None, "split %s 失败: %s" % (candidate, exc))
        # Last resort: the supervisor pane itself hosts the worker.
        return self.state.data.get("supervisor_pane")

    # -- the step ---------------------------------------------------------

    def run_step(self, step_id):
        """Run one step to a terminal state.

        Returns the step's final status: ``done`` / ``failed`` / ``blocked``.
        """
        settings = self.settings(step_id)
        marker = self.marker(step_id)
        step_started = time.time()
        attempts = {"resume": 0, "rebuild": 0}
        stalls = 0
        rearm = 0
        pane_id = self.ensure_worker_pane(step_id)
        settings["expect_rendered"] = self.render_expect(step_id, settings)

        self.state.update_step(
            step_id, status="running", role=settings["role"],
            kind=settings["kind"], model=settings["model"],
            agent_name=self.agent_name(step_id), pane_id=pane_id,
            attempts=dict(attempts), started_at=iso_now())

        prompt_path, prompt_text = self.write_prompt_file(step_id, settings)
        self.log(step_id, "提示词已写入 %s" % prompt_path)

        ok, diagnostic = self.start_agent(step_id, settings, pane_id)
        if not ok:
            self.log(step_id, "agent 启动失败: %s" % diagnostic)
            ok, diagnostic = self.submit_after_failed_start(step_id, settings)
            if not ok:
                return self.finish_failed(step_id, attempts, step_started,
                                          diagnostic or "agent 启动失败")
        else:
            ok, diagnostic = self.submit(step_id, pane_id)
            if not ok:
                self.log(step_id, "prompt 提交失败: %s" % diagnostic)
                return self.finish_failed(step_id, attempts, step_started, diagnostic)

        while True:
            verdict_payload = self.wait_step(step_id, settings, pane_id,
                                             marker, settings["timeout_ms"])
            verdict = verdict_payload.get("verdict", "unknown")
            self.log(step_id, "verdict=%s" % verdict)

            if verdict == "marker":
                if gate_ok(settings, self.run_dir, self.cwd):
                    return self.finish_done(step_id, attempts, step_started)
                # A marker without evidence is an echo, not progress — unless
                # the evidence itself says otherwise: a result file that
                # declares "failed" is a real verdict (§9.2 settled
                # semantics), not an echo, so the ladder starts right away.
                status, payload = result_status(self.run_dir, step_id)
                if status == "failed":
                    summary = self.summarize_failure(step_id, payload)
                    self.log(step_id, "标记命中但 result 报 failed，转入恢复梯")
                else:
                    # The wait is re-armed, but only a bounded number of
                    # times: a marker that keeps hitting while the gate
                    # stays closed would otherwise loop forever.
                    rearm += 1
                    self.log(step_id, "标记命中但 gate 未通过（回显误报 %d/%d），重新武装等待"
                             % (rearm, MAX_REARM))
                    self.append_step_log(step_id, "marker hit but gate failed")
                    if rearm < MAX_REARM:
                        continue
                    summary = "标记出现但交付证据始终缺失"
                action = ladder_action(attempts["resume"], attempts["rebuild"],
                                       "settled", settings["retries"],
                                       settings["rebuilds"])
                if action == "resume":
                    attempts["resume"] += 1
                    self.state.update_step(step_id, attempts=dict(attempts))
                    if self.apply_resume(step_id, settings, pane_id,
                                         attempts, summary):
                        rearm = 0
                        continue
                        stalls = 0
                    action = ("rebuild" if attempts["rebuild"] < settings["rebuilds"]
                              else "fail")
                if action == "fail":
                    return self.finish_failed(step_id, attempts, step_started, summary)
                rebuilt, pane_id = self.apply_rebuild(step_id, settings, pane_id,
                                                      attempts, summary)
                if not rebuilt:
                    return self.finish_failed(step_id, attempts, step_started,
                                              "重建失败")
                attempts["rebuild"] += 1
                self.state.update_step(step_id, attempts=dict(attempts),
                                       pane_id=pane_id)
                ok, diagnostic = self.submit(step_id, pane_id)
                if not ok:
                    return self.finish_failed(step_id, attempts, step_started,
                                              "重建后 prompt 提交失败: %s" % diagnostic)
                rearm = 0
                continue

            if verdict == "settled":
                status, payload = result_status(self.run_dir, step_id)
                if status == "done":
                    if gate_ok(settings, self.run_dir, self.cwd):
                        return self.finish_done(step_id, attempts, step_started)
                    # Same bound as the marker branch: the agent is idle and
                    # the next wait would return settled immediately, so an
                    # unbounded re-arm here spins forever without ever
                    # advancing or failing.
                    rearm += 1
                    self.log(step_id, "result 报告 done 但 gate 未通过（回显误报 %d/%d）"
                             % (rearm, MAX_REARM))
                    self.append_step_log(step_id, "result says done but gate failed")
                    if rearm < MAX_REARM:
                        continue
                    summary = "result 报告 done 但交付证据始终缺失"
                    action = ladder_action(attempts["resume"], attempts["rebuild"],
                                           "settled", settings["retries"],
                                           settings["rebuilds"])
                    if action == "resume":
                        attempts["resume"] += 1
                        self.state.update_step(step_id, attempts=dict(attempts))
                        if self.apply_resume(step_id, settings, pane_id,
                                             attempts, summary):
                            rearm = 0
                            continue
                        stalls = 0
                        action = ("rebuild" if attempts["rebuild"] < settings["rebuilds"]
                                  else "fail")
                    if action == "fail":
                        return self.finish_failed(step_id, attempts, step_started, summary)
                    rebuilt, pane_id = self.apply_rebuild(step_id, settings, pane_id,
                                                          attempts, summary)
                    if not rebuilt:
                        return self.finish_failed(step_id, attempts, step_started,
                                                  "重建失败")
                    attempts["rebuild"] += 1
                    self.state.update_step(step_id, attempts=dict(attempts),
                                           pane_id=pane_id)
                    ok, diagnostic = self.submit(step_id, pane_id)
                    if not ok:
                        return self.finish_failed(step_id, attempts, step_started,
                                                  "重建后 prompt 提交失败: %s" % diagnostic)
                    rearm = 0
                    continue
                summary = self.summarize_failure(step_id, payload)
                action = ladder_action(attempts["resume"], attempts["rebuild"],
                                       verdict, settings["retries"],
                                       settings["rebuilds"])
                if action == "resume":
                    attempts["resume"] += 1
                    self.state.update_step(step_id, attempts=dict(attempts))
                    if self.apply_resume(step_id, settings, pane_id,
                                         attempts, summary):
                        continue
                        stalls = 0
                    action = ("rebuild" if attempts["rebuild"] < settings["rebuilds"]
                              else "fail")
                if action == "fail":
                    return self.finish_failed(step_id, attempts, step_started, summary)
                # action == "rebuild"
                rebuilt, pane_id = self.apply_rebuild(step_id, settings, pane_id,
                                                      attempts, summary)
                if not rebuilt:
                    return self.finish_failed(step_id, attempts, step_started,
                                              "重建失败")
                attempts["rebuild"] += 1
                self.state.update_step(step_id, attempts=dict(attempts),
                                       pane_id=pane_id)
                ok, diagnostic = self.submit(step_id, pane_id)
                if not ok:
                    return self.finish_failed(step_id, attempts, step_started,
                                              "重建后 prompt 提交失败: %s" % diagnostic)
                continue

            if verdict == "blocked":
                self.log(step_id, "worker 等待人工输入，分支标记 blocked")
                self.notify_blocked(step_id)
                return self.finish_blocked(step_id, attempts, step_started)

            if verdict == "gone":
                summary = self.summarize_failure(step_id, None)
                action = ladder_action(attempts["resume"], attempts["rebuild"],
                                       verdict, settings["retries"],
                                       settings["rebuilds"])
                if action != "rebuild":
                    return self.finish_failed(step_id, attempts, step_started,
                                              "worker 已消失且重建次数用尽")
                rebuilt, pane_id = self.apply_rebuild(step_id, settings, pane_id,
                                                      attempts, summary)
                attempts["rebuild"] += 1
                self.state.update_step(step_id, attempts=dict(attempts),
                                       pane_id=pane_id)
                if not rebuilt:
                    return self.finish_failed(step_id, attempts, step_started,
                                              "重建失败")
                ok, diagnostic = self.submit(step_id, pane_id)
                if not ok:
                    return self.finish_failed(step_id, attempts, step_started,
                                              "重建后 prompt 提交失败: %s" % diagnostic)
                continue

            # timeout / unknown — read the现场 before deciding.
            summary = self.summarize_failure(step_id, None)
            if self.output_shows_progress(step_id, pane_id):
                stalls += 1
                self.log(step_id, "仍有推进迹象，继续等待（%d/%d）"
                         % (stalls, MAX_STALLS))
                if stalls < MAX_STALLS:
                    continue
                # 观察到上限仍无完成：一个等待一直异常、或输出恒定不变的
                # worker 不该无限等下去，转入恢复梯。
                self.log(step_id, "连续 %d 次观察无进展，转入恢复梯" % stalls)
            else:
                stalls = 0
            action = ladder_action(attempts["resume"], attempts["rebuild"],
                                   verdict, settings["retries"],
                                   settings["rebuilds"])
            if action == "resume":
                attempts["resume"] += 1
                self.state.update_step(step_id, attempts=dict(attempts))
                if self.apply_resume(step_id, settings, pane_id, attempts, summary):
                    continue
                    stalls = 0
                action = "rebuild" if attempts["rebuild"] < settings["rebuilds"] else "fail"
            if action == "rebuild":
                rebuilt, pane_id = self.apply_rebuild(step_id, settings, pane_id,
                                                      attempts, summary)
                attempts["rebuild"] += 1
                self.state.update_step(step_id, attempts=dict(attempts),
                                       pane_id=pane_id)
                if not rebuilt:
                    return self.finish_failed(step_id, attempts, step_started,
                                              "重建失败")
                ok, diagnostic = self.submit(step_id, pane_id)
                if not ok:
                    return self.finish_failed(step_id, attempts, step_started,
                                              "重建后 prompt 提交失败: %s" % diagnostic)
                continue
            return self.finish_failed(step_id, attempts, step_started, summary)

    # -- outcomes ---------------------------------------------------------

    def submit_after_failed_start(self, step_id, settings):
        """Try to submit anyway — the agent may exist but not be ready."""
        return self.submit(step_id, self.worker_panes.get(step_id))

    def append_step_log(self, step_id, message):
        path = self.run_dir / "logs" / ("%s.log" % step_id)
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write("%s %s\n" % (time.strftime("%H:%M:%S"), message))
        except OSError:
            pass

    def summarize_failure(self, step_id, payload):
        """A short failure summary for the handoff record."""
        if isinstance(payload, dict):
            for key in ("error", "summary", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value[:500]
        status, parsed = result_status(self.run_dir, step_id)
        if isinstance(parsed, dict):
            for key in ("error", "summary", "message", "reason"):
                value = parsed.get(key)
                if isinstance(value, str) and value:
                    return value[:500]
        return "（无错误摘要）"

    def output_shows_progress(self, step_id, pane_id):
        """Decide whether a timed-out worker is still making progress.

        The check is deliberately conservative: a live agent process in the
        pane is taken as progress, because the repository rule is to read the
       现场 rather than to guess from silence.
        """
        code, info = self.herdr.pane_process_info(pane_id)
        if code == 0 and info is not None:
            running = shell_processes(info)
            if running:
                return True
        text = self.herdr.pane_read(pane_id, source="visible", lines=60)
        if not text:
            return False
        marker = self.marker(step_id)
        return bool(text.strip()) and marker not in text

    def notify_blocked(self, step_id):
        self.herdr.notify(
            "hsuper 需要人工处理",
            "步骤 %s 在等待人工输入，其它分支已继续。" % step_id,
            sound="request")

    def finish_done(self, step_id, attempts, step_started):
        elapsed = round(time.time() - step_started, 1)
        self.state.update_step(step_id, status="done", attempts=dict(attempts),
                               duration_s=elapsed, finished_at=iso_now(),
                               reason=None)
        self.log(step_id, "完成（用时 %s）" % format_duration(elapsed))
        return "done"

    def finish_failed(self, step_id, attempts, step_started, reason):
        elapsed = round(time.time() - step_started, 1)
        self.state.update_step(step_id, status="failed", attempts=dict(attempts),
                               duration_s=elapsed, finished_at=iso_now(),
                               reason=reason)
        self.log(step_id, "失败: %s" % reason)
        self.herdr.notify("hsuper 分支失败",
                          "步骤 %s 失败: %s" % (step_id, reason), sound="request")
        return "failed"

    def finish_blocked(self, step_id, attempts, step_started):
        elapsed = round(time.time() - step_started, 1)
        self.state.update_step(step_id, status="blocked", attempts=dict(attempts),
                               duration_s=elapsed, finished_at=iso_now(),
                               reason="等待人工输入")
        return "blocked"

    # -- the run ----------------------------------------------------------

    def run(self, resume=False):
        """Run the flow.  Returns the process exit code.

        Branch isolation is the rule here: a step whose predecessor failed is
        skipped, and every other branch keeps going.  Independent steps run
        concurrently up to ``max_parallel`` (1 = strictly serial, the path
        the deterministic tests rely on); the dispatch loop polls for thread
        completion, which is scheduling, not the blind sleeping the
        coordinator rules forbid.
        """
        order, roots, _ = build_plan(self.flow)
        for step_id in order:
            settings = self.settings(step_id)
            self.state.update_step(
                step_id, status=self.state.status(step_id) or "pending",
                role=settings["role"], kind=settings["kind"],
                model=settings["model"])

        if resume:
            order = self.resume_order(order)
            self.log(None, "resume: 只重跑 %s" % ", ".join(order))
        else:
            for step_id in order:
                self.state.update_step(step_id, status="pending")

        failed = []
        blocked = []
        done = []
        skipped = []

        remaining = list(order)
        running = {}  # step_id -> Thread

        while remaining or running:
            # Reap finished threads and record their outcomes.
            for step_id in list(running):
                thread = running[step_id]
                thread.join(timeout=0.2)
                if thread.is_alive():
                    continue
                del running[step_id]
                status = self.step_results.pop(step_id, "failed")
                if status == "done":
                    done.append(step_id)
                elif status == "blocked":
                    blocked.append(step_id)
                else:
                    failed.append(step_id)
                if self.budget_ms and self.elapsed_ms() > self.budget_ms:
                    self.budget_exhausted = True
                    self.log(None, "全局超时预算耗尽，剩余步骤不再派工")

            # Skip unreachable steps, dispatch runnable ones.
            for step_id in list(remaining):
                blockers = self.blockers_of(step_id)
                unmet = [s for s in blockers
                         if self.state.status(s) in ("failed", "blocked", "skipped")]
                if unmet:
                    self.state.update_step(step_id, status="skipped",
                                           reason="前序未完成: %s" % ", ".join(unmet))
                    skipped.append(step_id)
                    remaining.remove(step_id)
                    self.log(step_id, "跳过（前序未完成）")
                    continue

                if self.budget_exhausted:
                    self.state.update_step(step_id, status="skipped",
                                           reason="全局超时预算耗尽")
                    skipped.append(step_id)
                    remaining.remove(step_id)
                    self.log(step_id, "跳过（全局超时预算耗尽）")
                    continue

                if resume and self.state.status(step_id) == "done":
                    self.log(step_id, "已 done，跳过")
                    done.append(step_id)
                    remaining.remove(step_id)
                    continue
                if resume and self.state.status(step_id) == "blocked":
                    self.log(step_id, "blocked，保持原状")
                    blocked.append(step_id)
                    remaining.remove(step_id)
                    continue

                # Dependencies must all be done before this step dispatches.
                if not all(self.state.status(s) == "done" for s in blockers):
                    continue
                if self.max_parallel and len(running) >= self.max_parallel:
                    continue  # worker slots full; wait for a reaper pass

                remaining.remove(step_id)
                running[step_id] = threading.Thread(
                    target=self._run_step_thread, args=(step_id,))
                running[step_id].start()
                if self.max_parallel != 1:
                    self.log(step_id, "并行派工 %d/%d"
                             % (len(running),
                                self.max_parallel if self.max_parallel else 0))

            if running:
                time.sleep(0.2)
            elif remaining:
                # Nothing is running and nothing dispatched: every remaining
                # step waits on a blocker that will never finish this pass
                # (a failed/blocked dependency already recorded above).  The
                # next pass marks them skipped.
                time.sleep(0.2)

        return self.exit_code(failed, blocked, done, skipped)

    def _run_step_thread(self, step_id):
        """Thread body: run one step to a terminal state."""
        try:
            self.step_results[step_id] = self.run_step(step_id)
        except UsageError as exc:
            self.log(step_id, "步骤异常: %s" % exc)
            self.step_results[step_id] = "failed"

    def elapsed_ms(self):
        return int((time.time() - self.started_at) * 1000)

    def resume_order(self, order):
        """Steps that ``--resume`` should re-run.

        A step is re-run when it is pending, running or failed, and when the
        steps it depends on are either already done or are being re-run in
        this same pass.  The selection is therefore closed under the
        dependency relation: a dependency always comes first in the
        topological order, so it is dispatched (and finished) before its
        dependent.  Done steps are skipped and not dispatched again; blocked
        steps keep their state.
        """
        selected = []
        for step_id in order:
            status = self.state.status(step_id)
            if status in ("done", "blocked"):
                continue
            blockers = self.blockers_of(step_id)
            if all(self.state.status(s) == "done" or s in selected
                   for s in blockers):
                selected.append(step_id)
        return selected

    def blockers_of(self, step_id):
        """The steps that must finish before ``step_id`` can start.

        ``next`` names the successors, so a step's blockers are the steps
        that list it in their own ``next``.
        """
        blockers = []
        for other, settings in self.settings_cache.items():
            if other != step_id and step_id in settings["next"]:
                blockers.append(other)
        return blockers

    def exit_code(self, failed, blocked, done, skipped):
        """The run's exit code.

        The table follows the acceptance criteria in the proposal: a branch
        that exhausted its recovery ladder is exit 20 even when other
        branches finished, because that is the actionable signal.  Exit 21 is
        a branch waiting for a human, exit 22 is the global budget running
        out, and exit 23 is a partial success without a hard failure.
        """
        if failed:
            return EXIT_BRANCH_FAILED
        if blocked:
            return EXIT_BRANCH_BLOCKED
        if self.budget_exhausted and not done:
            # The budget stops further dispatch; it does not retroactively
            # fail steps that finished.  A run where everything completed
            # before the budget ran out is a success.
            return EXIT_BUDGET
        if skipped:
            return EXIT_PARTIAL
        return EXIT_OK


# ---------------------------------------------------------------- CLI

def print_help():
    """Print the module docstring (the file header)."""
    print(__doc__ or "hsuper — see the file header for the contract.")


class UsageParser(argparse.ArgumentParser):
    """An argparse parser whose errors exit 1, not 2.

    The documented exit-code table (flows/_schema.md, scripts/README.md)
    records "启动/参数/flow 校验错误" as 1; argparse's own error path exits
    with 2, which callers relying on the table would misroute.
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        print("hsuper: %s" % message, file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def build_parser():
    parser = UsageParser(prog="hsuper.sh", add_help=False,
                         description="受监督派发：按声明流程执行并做有界恢复")
    parser.add_argument("--flow", metavar="NAME", help="从 flows/NAME.toml 加载流程")
    parser.add_argument("--flow-file", metavar="PATH", help="从任意路径加载流程")
    parser.add_argument("--run", metavar="RUN_ID", help="指定 run ID（配合 --resume）")
    parser.add_argument("--resume", action="store_true", help="从 state.json 续跑未完成步骤")
    parser.add_argument("--task", metavar="TEXT", help="本次任务描述，填入 {{task}}")
    parser.add_argument("-C", "--path", metavar="PATH", help="团队工作目录")
    parser.add_argument("--max-parallel", type=int, metavar="N", help="同时存活 worker 上限")
    parser.add_argument("--budget-ms", type=int, metavar="MS",
                        help="全局超时预算（毫秒）；默认按各步骤预算求和")
    parser.add_argument("--dry-run", action="store_true", help="只校验并打印计划，不创建任何资源")
    parser.add_argument("--foreground", action="store_true", help="不开 supervisor pane，直接在前台跑")
    parser.add_argument("--json", action="store_true", help="结束时输出 schema_version=1 JSON")
    parser.add_argument("--allow-unsafe-limits", action="store_true",
                        help="允许 retries>2 / rebuilds>1")
    parser.add_argument("--no-focus", action="store_true", help="不切换用户焦点")
    parser.add_argument("--in-pane", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("-v", "-V", "--version", action="version", version="hsuper %s" % version())
    parser.add_argument("-h", "--help", action="store_true", help="打印本帮助")
    return parser


def locate_flow(name):
    for base in (REPO_ROOT / "flows", Path.cwd() / "flows"):
        candidate = base / ("%s.toml" % name)
        if candidate.is_file():
            return candidate
    raise FlowError("找不到流程文件 %s.toml（已查找 %s）"
                    % (name, ", ".join(str(b) for b in (REPO_ROOT / "flows",
                                                        Path.cwd() / "flows"))))


def print_plan(flow_name, flow, order, roots, settings_for):
    lines = []
    lines.append("flow %s  步骤 %d" % (flow_name, len(order)))
    for step_id in order:
        settings = settings_for(step_id)
        lines.append("  %-10s role=%-8s kind=%-10s model=%-16s next=%s"
                     % (step_id, settings["role"], settings["kind"],
                        settings["model"] or "-", ",".join(settings["next"]) or "-"))
    return "\n".join(lines)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.help:
        print_help()
        return EXIT_OK

    if not args.flow and not args.flow_file and not args.run:
        parser.error("需要 --flow NAME / --flow-file PATH / --run RUN_ID 之一")

    herdr = Herdr()

    # --- locate the run directory -----------------------------------------
    # The --run form re-reads the flow from the run's own copy, so a resume
    # cannot silently pick up an edited flows/*.toml.
    resume_mode = bool(args.run) and not args.flow and not args.flow_file
    cwd = Path(os.path.abspath(os.path.expanduser(args.path))) if args.path \
        else Path.cwd()

    flow_name = ""
    flow = {}
    source_path = None
    run_dir = None
    state = None

    if resume_mode:
        run_dir = find_run_dir(cwd, args.run)
        if run_dir is None:
            print("hsuper: 找不到 run %s 的目录" % args.run, file=sys.stderr)
            return EXIT_USAGE
        state = State.load(run_dir / "state.json")
        flow_name = state.data.get("flow") or ""
        flow_path = run_dir / "flow.toml"
        if not flow_path.is_file():
            print("hsuper: run %s 缺少 flow.toml，无法续跑" % args.run,
                  file=sys.stderr)
            return EXIT_USAGE
        _, flow, flow_path = load_flow(flow_path)
        if not args.task:
            args.task = state.data.get("task", "")
        # The run's own cwd wins over the caller's, so a resume from another
        # directory still works on the right tree.
        if not args.path and state.data.get("cwd"):
            cwd = Path(state.data["cwd"])
    else:
        if args.flow_file:
            source_path = Path(args.flow_file)
        else:
            source_path = locate_flow(args.flow)
        flow_name, flow, source_path = load_flow(source_path)
        if args.flow and flow_name != args.flow:
            raise FlowError("流程文件里的名称是 %r，与 --flow %r 不一致"
                            % (flow_name, args.flow))

    # --- validate ----------------------------------------------------------
    known_kinds = herdr.agent_kinds()
    errors = validate_flow(flow, flow_name,
                           allow_unsafe_limits=args.allow_unsafe_limits,
                           known_kinds=known_kinds)
    if errors:
        for message in errors:
            print("hsuper: flow 校验失败: %s" % message, file=sys.stderr)
        return EXIT_USAGE

    order, roots, defaults = build_plan(flow, flow_name)
    settings_cache = {}

    def settings_for(step_id):
        if step_id not in settings_cache:
            settings_cache[step_id] = step_settings(flow, step_id, defaults)
        return settings_cache[step_id]

    if args.dry_run:
        print(print_plan(flow_name, flow, order, roots, settings_for))
        print("dry-run: 未创建任何资源")
        return EXIT_OK

    # --- create or adopt the run ------------------------------------------
    if state is None:
        if not args.task:
            parser.error("首次运行需要 --task TEXT")
        run_id = args.run or new_run_id()
        run_dir = make_run_dir(cwd, run_id)
        write_text_atomic(run_dir / "flow.toml", Path(source_path).read_text())
        state = State.create(run_dir / "state.json", run_id, flow_name,
                             args.task, cwd)
    elif not args.resume and not args.in_pane:
        print("hsuper: --run %s 需要配合 --resume" % args.run, file=sys.stderr)
        return EXIT_USAGE

    # --- create the resources (unless resuming) ---------------------------
    layout_code = flow.get("layout") or layout_for_steps(len(order))
    if args.in_pane:
        pass
    elif args.resume and state.data.get("workspace_id"):
        # A resumed run keeps its workspace and its pane mapping.
        pass
    else:
        created = create_resources(herdr, state, cwd, flow_name, args,
                                   len(order), layout_code)
        if created is None:
            return EXIT_USAGE
        map_steps_to_panes(herdr, state, order, layout_code)
        if not args.in_pane and not args.foreground:
            # Re-exec into the supervisor pane so the run is visible and
            # interruptible.  The parent only reports where it went.
            return launch_supervisor(herdr, state, run_dir, args)

    supervisor = Supervisor(herdr, state, flow, run_dir, args)
    if args.in_pane:
        # §8.2: inside the supervisor pane the output must be visible on the
        # pane AND land in the run directory, so the run survives even if
        # nobody watches the pane.
        supervisor.attach_tee(run_dir / "logs" / "supervisor.log")
    if not supervisor.budget_ms:
        # The default budget is the sum of what the steps could take: each
        # step gets its timeout for the first attempt, for each resume and
        # for the rebuild.
        total = 0
        for step_id in order:
            settings = settings_for(step_id)
            total += settings["timeout_ms"] * (1 + settings["retries"]
                                               + settings["rebuilds"])
        supervisor.budget_ms = total
    started = time.time()
    code = supervisor.run(resume=args.resume)
    elapsed = round(time.time() - started, 1)

    report = format_report(state, elapsed)
    print(report)
    if args.json:
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "exit_code": code,
            "run_id": state.data.get("run_id"),
            "flow": state.data.get("flow"),
            "elapsed_s": elapsed,
            "run_dir": str(run_dir),
            "steps": [{"id": sid, "status": (state.data["steps"].get(sid) or {}).get("status"),
                       "reason": (state.data["steps"].get(sid) or {}).get("reason")}
                      for sid in state.data.get("steps", {})],
            "deviations": state.data.get("deviations", []),
        }, ensure_ascii=False))
    return code


def find_run_dir(cwd, run_id):
    """Locate a run directory by its run id.

    The caller's cwd comes first, then its parents (a resume from a project
    subdirectory finds the run without needing ``-C``).  From a completely
    unrelated directory, the caller must point at the project with ``-C`` —
    the state file that carries the run's own cwd lives inside the run
    directory, so it cannot be consulted before the directory is found.
    """
    base = Path(cwd)
    for ancestor in [base, *base.parents]:
        root = ancestor / RUNS_DIRNAME
        if not root.is_dir():
            continue
        for candidate in sorted(root.iterdir()):
            if candidate.is_dir() and candidate.name.endswith("-%s" % run_id):
                return candidate
    return None


def map_steps_to_panes(herdr, state, order, layout_code):
    """Assign one worker pane to each step, in visual order.

    The visual order is read from herdr's layout rectangles, so the first
    step lands in the top-left pane no matter how the layout was built.
    """
    worker_panes = state.data.get("worker_panes") or []
    if not worker_panes:
        raise UsageError("尚未创建 worker pane")
    panes = [{"pane_id": pane_id} for pane_id in worker_panes]
    ordered = visual_order(herdr, panes)
    if len(ordered) < len(order):
        raise UsageError("worker pane 数量 (%d) 少于步骤数量 (%d)"
                         % (len(ordered), len(order)))
    mapping = {}
    for step_id, pane in zip(order, ordered):
        mapping[step_id] = pane["pane_id"]
    state.data["step_panes"] = mapping
    state.save()
    return mapping


def create_resources(herdr, state, cwd, flow_name, args, step_count, layout_code):
    """Create the workspace, the supervisor tab and the worker panes.

    Three-part structure: a workspace, a supervisor tab (one pane) and a
    worker tab (one pane per step).  ``_h_build_layout`` is deliberately not
    used here — it creates the workspace and every pane in a single tab, which
    is a different shape than this needs.
    """
    label = "hsuper-%s" % flow_name
    workspace_id, root_pane, tab_id = herdr.create_workspace(cwd, label)

    # Rollback bookkeeping: anything created after the workspace must be
    # torn down if a later step fails — no half-built topology may survive.
    closed = {"workspace": False}
    tabs = []
    panes = []

    def rollback():
        for pane_id in panes:
            herdr.close_pane(pane_id)
        for tab_id in tabs:
            herdr.close_tab(tab_id)
        if not closed["workspace"]:
            herdr.close_workspace(workspace_id)
            closed["workspace"] = True

    try:
        supervisor_tab, supervisor_pane = herdr.create_tab(workspace_id, cwd, "supervisor")
        tabs.append(supervisor_tab)
        herdr.rename_pane(supervisor_pane, "supervisor")
        panes.append(supervisor_pane)

        worker_tab, worker_root = herdr.create_tab(workspace_id, cwd, "workers")
        tabs.append(worker_tab)

        # Build the worker layout with hopen.sh's own split plan.
        count, plan = layout_plan(layout_code)
        created = [worker_root]
        for parent_token, direction, ratio in plan:
            parent = resolve_split_parent(parent_token, created, None)
            pane_id = herdr.split_pane(parent, direction, ratio)
            created.append(pane_id)
            panes.append(pane_id)
    except UsageError as exc:
        print("hsuper: 建资源失败，回滚: %s" % exc, file=sys.stderr)
        try:
            rollback()
        except UsageError as roll_exc:
            print("hsuper: 回滚也失败，可能留下半建资源: %s" % roll_exc,
                  file=sys.stderr)
        return None

    state.data["workspace_id"] = workspace_id
    state.data["tab_id"] = worker_tab
    state.data["supervisor_pane"] = supervisor_pane
    state.data["supervisor_tab_id"] = supervisor_tab
    state.data["worker_root_pane"] = worker_root
    state.data["worker_panes"] = created
    state.data["layout"] = layout_code
    state.save()
    return workspace_id


def resolve_split_parent(parent_token, created, previous):
    """Resolve hopen.sh's parent tokens (ROOT / LAST / PANE<n>).

    ``LAST`` means the pane the previous split *created* (hopen.sh's ``pid``
    holds the split's return value), not the previous parent — the two read
    differently on every layout that chains a split under a split (12, 13,
    22, 31, 111, 221, 122).
    """
    if parent_token == "ROOT":
        return created[0]
    if parent_token == "LAST":
        return created[-1]
    if parent_token.startswith("PANE"):
        index = int(parent_token[4:])
        if index < 0 or index >= len(created):
            raise UsageError("hopen.sh 布局代号引用了不存在的 pane: %s" % parent_token)
        return created[index]
    raise UsageError("未知的布局 parent token: %s" % parent_token)


def launch_supervisor(herdr, state, run_dir, args):
    """Re-exec the supervisor inside its own pane.

    The re-exec is what makes the run visible and interruptable.  It carries
    ``--in-pane`` so the child skips resource creation and goes straight into
    the supervision loop; the child never re-enters this path, so there is no
    recursion.
    """
    supervisor_pane = state.data.get("supervisor_pane")
    if not supervisor_pane:
        raise UsageError("缺少 supervisor pane，无法启动监督循环")
    command = "python3 %s --run %s --in-pane --path %s" % (
        (SCRIPTS_DIR / "hsuper.py").resolve(),
        state.data["run_id"],
        quote_path(state.data.get("cwd") or os.getcwd()))
    herdr.run_in_pane(supervisor_pane, command)
    print("run=%s ws=%s supervisor_pane=%s run_dir=%s"
          % (state.data["run_id"], state.data.get("workspace_id"),
             supervisor_pane, run_dir))
    return EXIT_OK


def quote_path(path):
    """Quote a path for a shell command line."""
    return "'%s'" % str(path).replace("'", "'\\''")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FlowError, UsageError) as error:
        print("hsuper: %s" % error, file=sys.stderr)
        sys.exit(EXIT_USAGE)
    except KeyboardInterrupt:
        print("hsuper: 已中断", file=sys.stderr)
        sys.exit(EXIT_USAGE)
