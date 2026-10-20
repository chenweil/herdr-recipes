---
status: accepted
date: 2026-10-21
---

# Coordinate an Explicit Team Across Tabs and Worktrees

Users want one main conversation while the agent chooses useful parallel work. Extend the
coordinator's prompt from same-tab control to resources it created or the user explicitly
assigned, including worktree workspaces. Reuse the same template and context builder for hteam
and in-place skill entry. Domain skills supply work methods; all worker delegation uses Herdr.
Research is an example, not a special command or keyword parser.

## Resource and execution contract

- Default worker kind follows the coordinator; native model/account/permission settings remain
  unchanged unless explicitly configured. Default live-worker budget is four across the team,
  including starting and standby workers, excluding the coordinator and ordinary processes.
- Current explicit instructions override team configuration, which overrides defaults. An
  explicit larger count is a per-request override; the model cannot raise its own budget.
  These are prompt obligations verified against live resources, not a sandbox or hard limit.
- Keep the main tab as the human entry, group related work in a work tab, and preserve focus.
  Independent parallel code changes use worktrees from an explicit baseline. Unresolved
  dependence on dirty files requires a concrete baseline decision rather than silent copying.
- Complete an authorized task through dispatch, inspection, correction and verified delivery
  during the current execution. A standby request only prepares workers. Failure preserves
  diagnostics; independent work may continue. No silent kind/count substitution is accepted.
- The coordinator writes local execution records of ownership, task IDs, mappings and evidence.
  They are notes, not queues, issue trackers or authoritative live state. Reconcile with Herdr,
  Git and outputs after context loss; no automatic wakeup/restart recovery is promised.
- A code implementation task permits necessary local commits and integration, preferably in
  a separate integration worktree, followed by combined checks. Protect unrelated changes;
  read-only instructions and existing push/publish authorization boundaries still apply.
- Workers default to standby after delivery. Reclaim only team-owned temporary worker panes
  whose work is complete and archived when capacity is needed. Preserve explicitly retained
  resources; deleting worktrees or branches requires a separate explicit instruction.

## Consequences

The shell remains a launcher: configuration validation and exact context injection are
deterministic; planning, topology, work and evidence judgment remain model behavior. Existing
fixed Recipes retain their dispatch semantics. This supersedes only ADR-0002's same-tab scope
and narrows ADR-0001's exclusion to repository-owned runtimes. Strong enforcement, persistent
scheduling and recovery remain separate decisions. Launcher tests cannot prove model behavior;
live isolated scenarios must report exactly which paths they exercised.
