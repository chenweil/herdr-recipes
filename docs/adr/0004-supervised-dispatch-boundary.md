---
status: accepted
date: 2026-10-28
---

# Supervised Dispatch: Bounded Retry/Rebuild Inside the Launcher

## Context

ADR-0001 keeps retries and recovery outside the launcher boundary. In practice the
conversational coordinator (ADR-0003) already performs bounded recovery, but as model
behavior: it must be waiting, remember the task, and be otherwise free at the moment a
worker dies. Users report flows that break silently on worker error with no retry or
restart.

## Decision

Add one deterministic supervisor (`hsuper`) that executes the recovery policy already
documented in `coordinator-prompt.md` §5, for a single declarative flow:

- Scope is one run, inside one Herdr session, launched into a dedicated supervisor pane.
- Retry limits are the existing §5 numbers: 2 original-session resumes, then 1 rebuild,
  then the branch is marked failed and other branches continue.
- Flow definitions are a flat directed `next` chain, not a DAG expression language.
- State is written per run so `--resume` can continue the same run after a restart.

It does not add a background daemon, a task queue, cross-session auto-resume, YAML
conditionals, or automatic publishing.

## Consequences

- ADR-0001's prohibition narrows from "no retries at all" to "no retry runtime outside a
  live supervised run". Documentation stating the launcher never retries must be updated.
- The 2+1 ladder becomes executable policy; changing it requires an ADR amendment, not a
  prompt edit. Over-limit values are rejected unless `--allow-unsafe-limits`.
- Recovery is bounded and attributable: every attempt lands in `state.json` and
  `handoff/`. A dead supervisor pane is not auto-restarted.
- The conversational coordinator remains the entry point for exploratory work; `hsuper`
  only serves shapes the user has committed to a flow file.

## Measured facts this decision relies on

Herdr 0.9.5, measured on a disposable server (see `docs/herdr-research.md` §11):

- An agent that exits leaves its pane back at the shell prompt with the original
  `shell_pid`, so the same pane can host a replacement.
- Herdr keeps the agent **name** reserved for a few seconds after the process is gone.
  A same-name start that races the release fails with `agent_name_taken`; the rebuild
  therefore stops the old session first and waits for the name to be released. When the
  name cannot be released, the rebuild falls back to a new name and records a deviation.
  Stopping a live worker means killing its process group: ctrl+c does nothing to an idle
  pi TUI (measured twice), so send-keys is not a stop mechanism.
- A freshly split pane may not be at its shell prompt yet; starting an agent there
  fails with `agent_pane_busy`. The supervisor waits for the shell before the first
  start. The pane's `foreground_processes` holds the agent and its helper children,
  and right after `agent start` returns it can still show shell-init processes —
  recovery code must verify the agent body is actually there before killing.
- `herdr agent prompt --timeout` requires `--wait` (a bare --timeout is rejected with
  exit 2). The default settled match waits for the whole turn (40–60s for a first pi
  turn), so the submission uses `--wait --until working --until done`; a stall
  (`agent_prompt_stalled`, the submission written but no activity within 5s) is
  retried once and then left to the dual-signal wait.
- `herdr agent start` reports `agent_not_ready` when the agent is blocked during
  startup (an onboarding prompt). The name stays usable for `agent read` and
  `agent send-keys`.
- `herdr agent start … -- --model <m>` passes the model to the agent; `pi` shows the
  selected model in its status bar.
- `pane wait-output` searches the existing snapshot before it polls, so a marker that is
  already on screen matches immediately. The marker is therefore only a candidate: a
  step advances on evidence (`expect` plus a parseable `result/<step>.json`), never on
  the marker alone.
- `agent start` on a pane that is not at a shell prompt fails with `agent_pane_busy`.
