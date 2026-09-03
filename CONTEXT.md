# Herdr Recipes

Herdr Recipes describes reusable terminal arrangements that prepare agents for interactive work without owning the lifecycle of the work itself.

## Language

**Recipe**:
A reusable description of a terminal layout, its optional agent assignments, and their optional initial prompts.
_Avoid_: Workflow, job, pipeline

**Recipe Launcher**:
The component that prepares and dispatches a Recipe while leaving subsequent work under human or external Workflow control.
_Avoid_: Scheduler, orchestrator, workflow engine

**Dispatch**:
The attempt to start a Recipe's configured agents and submit their initial prompts. A Dispatch may partially succeed and does not imply that agent work has finished.
_Avoid_: Execution, completion

**Recipe Ready**:
A Dispatch result in which the layout exists, every configured agent started, and every configured initial prompt was submitted. It does not mean the agents completed their assigned work.
_Avoid_: Task complete, workflow complete, agents done

**Workflow**:
A sequence or graph of work that owns completion criteria, dependencies, waiting, retries, recovery, and result collection.
_Avoid_: Recipe, layout
