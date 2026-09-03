---
status: accepted
date: 2026-09-03
---

# Keep Recipes Separate from Workflows

## Context

Herdr Recipes prepares terminal arrangements for interactive work. A Recipe describes the
arrangement; a Recipe Launcher dispatches it. Neither concept needs to own the lifecycle of the
work that agents perform after dispatch.

## Decision

herdr-recipes remains a Recipe Launcher: it creates layouts, starts configured agents, and
submits optional initial prompts, but it does not own completion, dependency scheduling, retries,
recovery, or result collection. Those responsibilities stay with humans or external Workflow
tools such as herdr-workflows. This keeps the repository's interactive launcher boundary distinct
from a workflow scheduler.

## Consequences

- A successful **Recipe Ready** notification reports setup and dispatch success, not completed agent work.
- Initial prompts are submitted without waiting for the corresponding work to finish.
- Workflow integrations begin as documentation-only composition examples and add runtime code only after a concrete unmet need is recorded.
- Worktree orchestration, DAG execution, retry/resume, collection, and cleanup remain outside the current product boundary.
