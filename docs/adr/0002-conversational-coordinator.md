---
status: accepted
date: 2026-10-19
amended: 2026-10-21
---

# Allow a Conversational Coordinator Without a Workflow Runtime

## Context

Users find manually choosing layouts and per-pane prompts cumbersome. They want one main
conversation to decide how many workers to prepare, delegate work later, and inspect results.
The user explicitly chose conversational coordination over a background task engine.

ADR-0001 keeps runtime scheduling, completion ownership, retries and recovery out of the
Recipe Launcher. The development suggestions treated every coordinator as a Workflow engine;
an interactive agent role needs a narrower distinction.

## Decision

Provide an optional hteam launcher that prepares one coordinator pane and injects a readable
role prompt with exact workspace/tab/pane context. ADR-0003 expands its initial same-tab scope
to an explicitly owned team across tabs and worktree workspaces. The coordinator may use Herdr
CLI to create/reuse workers, submit task-specific prompts, read output and summarize evidence
during the user's conversation. Workers may be prepared in standby.

The repository's scripts still only prepare and Dispatch the coordinator. They do not run a
background scheduler, persist task queues, run retry/recovery services, infer acceptance from
agent state, or own task completion. The interactive agent may inspect, correct and continue
work during the current execution, keeping local evidence notes. Those decisions remain part
of the user's conversation. This clarifies ADR-0001; it does not adopt a Workflow runtime.

## Consequences

- Existing numbered layouts, per-pane prompt entry points and hstatus remain supported.
- The coordinator starts alone; there is no compulsory worker count or per-worker CLI setup.
- Worker layout/delegation choices are model behavior guided by an inspectable template,
  not deterministic guarantees of a shell launcher.
- Explicit team membership and preservation of the user's focus are coordinator obligations.
- Worker idle/done and prompt waiting do not establish task acceptance; summaries need evidence.
- Disconnected or lost conversations have no promised task recovery. Durable orchestration,
  automatic retries and a background execution service require a separate decision.
