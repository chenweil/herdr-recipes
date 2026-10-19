# herdr-recipes

Personal recipes for [herdr](https://herdr.dev) — key bindings, layout presets, and helper scripts, packaged for one-command install on any machine.

## What's in here

| File | Purpose |
|---|---|
| `scripts/hopen.sh` | Open a numbered-pane layout (`11`, `12`, `21`, `22`, `13`, `31`, `111`, `221`, `122`) in a new workspace, optionally dispatching agents from `hopen-agents.conf` |
| `scripts/hopen-agents.conf` | Per-layout / per-pane agent dispatch (which pane gets `claude` / `codex` / `pi` / …) plus optional `pane_name` |
| `scripts/hteam.sh` | Start one conversational coordinator that prepares workers and delegates through Herdr |
| `scripts/coordinator-prompt.md` | Inspectable instructions for worker preparation, later delegation and evidence-based summaries |
| `skills/herdr-recipes/SKILL.md` | Natural-language entry to Recipes and conversational coordination, alongside the global herdr skill |
| `scripts/hstatus.sh` | Read-only pane and agent snapshot for the caller or an explicit workspace, with text or JSON output |
| `scripts/agent-audit.py` | Read-only Herdr version, kind, executable, and integration audit |
| `scripts/herdr-pane-switch.py` | Switch to the N-th pane (1-based) in the active workspace |
| `scripts/version.sh` | Shared version reader (`-v` / `-V` / `--version`) |
| `scripts/README.md` | Detailed docs for the hopen / hopen-agents scripts |
| `config/keys.toml` | All `[[keys.command]]` entries this repo manages |
| `config/agent-catalog.json` | Versioned supported-kind, executable, integration, and alias catalog |
| `install.sh` | One-shot installer / upgrader / uninstaller |
| `hopen-once.sh` | Ad-hoc layout + per-pane kinds via command line (no chord, no conf) |
| `VERSION` | Single source of truth for the version number |
| `CHANGELOG.md` | What changed in each release |

## Version

Every script accepts `-v`, `-V`, or `--version`:

```bash
./install.sh --version                  # herdr-recipes 0.8.0
scripts/hopen.sh -v                     # hopen 0.8.0
scripts/hopen-once.sh -v                # hopen-once 0.8.0
scripts/herdr-pane-switch.py -v         # herdr-pane-switch 0.8.0
```

The number lives in `VERSION` at the repo root; `scripts/version.sh` is the single
reader all shell scripts source. See [CHANGELOG.md](CHANGELOG.md) for release notes.

## Conversational team (`hteam.sh`)

Start one main agent, then tell it what you need:

```bash
bash scripts/hteam.sh                         # default coordinator: codex
bash scripts/hteam.sh -k pi                    # use pi as the coordinator
bash scripts/hteam.sh -p "先准备两个 codex 和一个 pi，暂时不要工作"
bash scripts/hteam.sh -C /path/to/project -p "拆分登录功能，安排实现、测试和审查"
```

Talk to the main pane afterward: “把登录实现交给第一个 worker，第二个补测试”，
then “汇总进度和验证结果”. The coordinator can prepare idle workers first and delegate
later, choosing how many panes to open or reuse in the same tab. The main pane keeps
the left half when workers are added; workers share the right half.
Three workers use one top pane and two side-by-side bottom panes; four use a 2×2 grid.
The coordinator must inspect actual pane rectangles before reporting or drawing the layout.

The launcher bootstraps one coordinator only. Worker management is performed by the
interactive agent under [the coordinator instructions](scripts/coordinator-prompt.md),
with exact workspace/tab context. No background queue, automatic retries or task recovery
is added. Startup success is Recipe Ready, not completion of the delegated work.
See [ADR-0002](docs/adr/0002-conversational-coordinator.md). The pi-coordinator/pi-worker
standby → later delegation → result summary flow was verified on Herdr 0.9.5;
other agent kinds remain subject to their tool and approval configuration.

## Project skill (`herdr-recipes`)

The [project skill](skills/herdr-recipes/SKILL.md) complements the global `herdr` skill:
it chooses the Recipe/team/status entry point and shares the coordinator instructions.
Inside Herdr, the current agent can become the coordinator; an explicit new-team request
uses `hteam.sh`. The `-k` on that launcher selects the main agent, while the request determines
worker kinds and count. General Herdr CLI knowledge stays in the global skill.

Enable project discovery with a local link from the repository root (leave any existing
`herdr-recipes` entry intact and inspect it first):

```bash
mkdir -p .agents/skills
ln -s ../../skills/herdr-recipes .agents/skills/herdr-recipes
```

The source is tracked in `skills/`; `.agents/` stays local and ignored. Refresh the agent's
skill list or start a new conversation if needed. Other agent hosts can load the canonical
`SKILL.md` explicitly or link the same source in their own skill directory. `install.sh`
continues to install scripts/key bindings; it does not install this skill globally.

Example requests after loading `$herdr-recipes`:

- “在当前 tab 先准备两个 codex 和一个 pi，全部待命。”
- “分析这个需求，由你决定需要几个 worker，最多三个，先只读分析。”
- “新建一个 pi 主协调团队，先准备两个 codex，暂时不要工作。”
- “让实现员处理 T1，复核员等实现后审查，读取实际结果后汇总。”

For manual acceptance, run the first request in a Herdr agent: verify that the caller's
workspace/tab and existing panes are preserved, three workers receive standby roles, and
no implementation work starts. Then delegate a small read-only task and check worker output
before accepting the coordinator's summary. These skill-driven paths still need live model
validation; the earlier hteam pi/pi smoke covers the launcher path, not skill discovery.

## Workspace status (`hstatus.sh`)

```bash
scripts/hstatus.sh                         # caller's workspace, from a Herdr pane
scripts/hstatus.sh --workspace w3          # explicit workspace, also outside Herdr
scripts/hstatus.sh --workspace w3 --json   # schema_version=1 JSON snapshot
```

Shows every pane, including plain shells, with its label, detected agent kind/name,
and current status. It reads existing state without changing focus or configuration.
Pane order follows `(y,x)` geometry; if a layout is unavailable or covers only one
of several tabs, it reports `pane_order=pane_id` and sorts by exact pane ID instead.
A status snapshot does not establish that agent work or a Workflow has completed.
See [the script reference](scripts/README.md#hstatussh--只读-workspace-状态摘要).

## Key bindings (with default `cmd+b` prefix)

| Chord | Action |
|---|---|
| `prefix 1..6` | Switch to pane N in active workspace |
| `prefix alt 1..9` | Open hopen layout (auto-start agents) — `1=12, 2=21, 3=22, 4=13, 5=31, 6=111, 7=11, 8=221, 9=122` |
| `prefix ctrl 1..9` | Open hopen layout (bare panes, no agents) — same codes |

## Layouts

Each digit is the number of panes in one column, read from left to right:

| Code | Panes | Visual | Position names |
|---|---|---|---|
| `11` | 2 | `[A][B]` | left / right |
| `12` | 3 | `[A][B/C]` | left / right-top / right-bottom |
| `21` | 3 | `[A/B][C]` | left-top / right / left-bottom |
| `111` | 3 | `[A][B][C]` | left / middle / right |
| `13` | 4 | `[A][B/C/D]` | left / right-top / right-mid / right-bottom |
| `31` | 4 | `[A/B/C][D]` | left-top / right / left-mid / left-bottom |
| `22` | 4 | `[A/B][C/D]` | left-top / right-top / left-bottom / right-bottom |
| `221` | 5 | `[A/B][C/D][E]` | left-top / middle-top / right / left-bottom / middle-bottom |
| `122` | 5 | `[A][B/C][D/E]` | left / middle-top / right-top / middle-bottom / right-bottom |

`221` and `122` pair a full-height pane with a 2×2 grid, each occupying 50% of
the workspace width. `221` has the grid on the left (column widths 25% / 25% / 50%);
`122` has the grid on the right (50% / 25% / 25%). Terminal rounding may differ
by one cell.

`111` preserves column widths of 50% / 25% / 25%.

Panes within a column always split that column evenly. `13` and `31` are the only
layouts with three panes in one column, so they pass an explicit split ratio
instead of splitting twice in half — a plain double split would give that column
50% / 25% / 25%. On a 40-row column the three panes come out 13/14/13; the exact
numbers follow the terminal height, but the three stay within one row of each other.

Position names are what you use in `hopen-agents.conf` section headers and what
unnamed panes fall back to as their label.

See `scripts/README.md` for the full mapping and creation-order caveats
(`21`, `31`, `22`, `221`, `122` create panes in a different order than they appear).

## Ad-hoc layout via command line (`hopen-once.sh`)

If the chord-based flow is hard to remember, run `hopen-once.sh` directly from any shell:

```bash
# Auto-pick layout from N kinds: 2 → 11 (side by side), 3 → 12 (left + right column), 4 → 22 (2x2)
hopen-once.sh codex pi                      # 2 panes, layout 11
hopen-once.sh codex codex pi                # 3 panes, layout 12
hopen-once.sh codex codex codex claude      # 4 panes, layout 22

# Short aliases (resolved by _resolve_kind): op/cc/cd
hopen-once.sh op cd pi                       # 3 panes, layout 12

# Explicit layout when auto-pick doesn't fit
hopen-once.sh -l 13 codex codex codex pi     # 4 panes, layout 13
hopen-once.sh -l 11 codex pi                 # 2 panes, left/right split
hopen-once.sh -l 221 -k codex:5              # 5 panes, [A/B][C/D][E]
hopen-once.sh -l 122 -k codex:5              # 5 panes, [A][B/C][D/E]

# Repeat the same kind N times with `K:N` (saves typing `-k` N times)
hopen-once.sh -l 22 -k codex:4               # 4 panes, all codex
hopen-once.sh -l 22 -k codex:2 -k pi:2       # 2 codex + 2 pi (2x2)
hopen-once.sh codex:4                       # positional also supports `K:N`

# Open workspace in another directory (relative or absolute)
hopen-once.sh -C /tmp -l 22 -k codex:4      # absolute path
hopen-once.sh -C ../project -l 22 -k codex:4 # relative path (from current cwd)
hopen-once.sh -C ~ -l 22 -k codex:4         # home dir
# Default cwd is `.` (current directory), so omit -C to stay where you are.

# Workspace + tab naming: ws label and tab title are auto-derived from
# the workspace cwd:
#   - git repo cwd  → git branch name (e.g. "main", "feature/foo")
#   - non-repo cwd  → basename of cwd (e.g. "herdr-recipes", "tmp")
# So multiple workspaces open side-by-side are easy to tell apart.

# Per-pane prompts (matched by index to kinds)
hopen-once.sh -l 22 \
  -k codex -p "implement A" \
  -k codex -p "implement B" \
  -k pi   -p "review"

# Per-pane labels (-N/--pane-name, visual order, matched by index to kinds)
hopen-once.sh -l 22 \
  -k codex -N "codex-A" -p "implement A" \
  -k codex -N "codex-B" -p "implement B" \
  -k pi   -N "review"   -p "review"

# Comma-separated shortcut: -N "A,B,C" == -N A -N B -N C
hopen-once.sh -l 22 -k codex:4 -N "a,b,c,d"

# Positional overflow: KINDS past max_panes go into NAMES (visual order)
# NAMES[i] lands on the (i+1)-th visual position, not the i-th pane ID.
hopen-once.sh -l 21 pi pi codex pi-top pi-right cd-bottom
#   pi-top   -> visual 1 (left-top),   pi-right -> 2 (right),
#   cd-bottom -> visual 3 (left-bottom)
hopen-once.sh -l 22 codex codex pi claude A B
#   A -> visual 1 (left-top), B -> 2 (right-top)
#   visual 3 and 4 get no name, so they fall back to left-bottom / right-bottom

# Bare workspace, no agents (also useful for jumping to a fresh layout)
hopen-once.sh -n -l 22
```

Flag reference: `-l/--layout`, `-k/--kind`, `-p/--prompt`, `-N/--pane-name`, `-n/--no-agents`, `-C/--path`, `-v/--version`, `-h/--help`. Both long and short forms are accepted; mixed forms (e.g. `-n --layout 22`) work too.

`hopen-once.sh` shares layout/split/agent primitives with `hopen.sh` (`_h_build_layout`, `_start_agent`, etc.) and inherits the kind alias table. It bypasses `hopen-agents.conf` entirely — the kinds you pass are the kinds that get started, no merge with any template.

## Compose with an external Workflow

This repository is a Recipe Launcher: it prepares a layout and performs Dispatch. It does not
wait for agent work, retry steps, collect results, or clean up workspaces. Those responsibilities
can be delegated to the optional [herdr-workflows](https://github.com/aorumbayev/herdr-workflows)
plugin; installing that plugin is not required by this repository.

For example, prepare a bare Recipe, then run an external review Workflow:

```bash
bash scripts/hopen-once.sh --layout 22 --no-agents

# Optional external Workflow tool: owns the later run/wait/review lifecycle.
hwf run review
```

The corresponding `.hwf/workflows/review.yaml` can stay minimal. The YAML shape below follows the
upstream [workflow example](https://github.com/aorumbayev/herdr-workflows#write-your-first-workflow);
this repository does not validate or execute the file, so re-check the upstream contract before use:

```yaml
version: v1alpha1
steps:
  - id: diff
    run: [git, diff, HEAD]
  - agent: |
      Review this diff and report blocking issues only.

      {{steps.diff.stdout}}
    using: claude
    when: "{{steps.diff.stdout}}"
    pane:
      open: beside
```

The Workflow owns waiting and completion; the `Recipe Ready` result from this repository only
means that layout setup and configured Dispatch succeeded (the notification title is `Recipe ready`).

Kinds are placed in panes in visual reading order (left-to-right, top-to-bottom). Layout 22 with `codex codex pi claude` gives you:
- top-left → codex
- top-right → codex
- bottom-left → pi
- bottom-right → claude

## Install (one-time, per machine)

Prerequisites: Herdr `0.8.2+` already installed (`0.9.5` recommended — earlier versions
lose `prefix+alt` / `prefix+ctrl` shortcuts under a Chinese/Japanese/Korean IME, see
`docs/review-2026-10-19.md`), `python3` in PATH (used by the pane switcher,
agent audit, and installer). Running the test suite additionally needs Python 3.11+ (uses
`tomllib`); the runtime scripts themselves do not. The installer reports optional
executable/integration gaps but never installs integrations.

```bash
git clone https://github.com/chenweil/herdr-recipes.git ~/playground/herdr-recipes
cd ~/playground/herdr-recipes
./install.sh
```

`install.sh` does the following, in order:

1. Symlinks `~/.config/herdr/scripts/` → `<repo>/scripts/`. If a `scripts/` directory already exists, it's renamed to `scripts.bak.<timestamp>` first.
2. Backs up `~/.config/herdr/config.toml` to `config.toml.bak.<timestamp>` (using `cp -p` to preserve permissions and mtimes). If that timestamp already exists from a same-second re-run, a `-1` / `-2` / … suffix is appended so no previous backup is overwritten.
3. Removes any pre-existing managed block (delimited by `# >>> herdr-recipes managed: begin >>>` / `# <<< ... end <<<`) from `~/.config/herdr/config.toml`.
4. Removes any legacy `[[keys.command]]` blocks matching the patterns this repo manages (`prefix+1..6`, `prefix+(alt|ctrl)+1..9`). This makes upgrading from a manual setup clean.
5. Bootstraps `prefix = "cmd+b"` inside the `[keys]` section if no `prefix =` line is already set. Override the default via `PREFIX_DEFAULT=ctrl+b ./install.sh`.
6. Inserts the managed block (the contents of `config/keys.toml`) inside the `[keys]` section, immediately before the next top-level section (`[experimental]`, `[ui]`, `[theme]`, …).
7. Runs `herdr config check`, then `herdr server reload-config`.
8. Runs the read-only agent catalog audit. Missing optional executables or integrations are warnings.

After install, the new key bindings are live in your running herdr session.

## Update

```bash
cd ~/playground/herdr-recipes
git pull
./install.sh
```

The marker block is replaced in-place; everything outside it (`[ui]`, `[experimental]`, `[theme]`, your own bindings) is left untouched.

## Uninstall

```bash
cd ~/playground/herdr-recipes
./install.sh --uninstall
```

This first backs up `~/.config/herdr/config.toml` to `config.toml.bak.<timestamp>` (same naming rule as install), then removes the managed key block, unlinks `scripts/` (only if it still points at this repo), and reloads the herdr config. Files previously backed up to `scripts.bak.<timestamp>` and `config.toml.bak.<timestamp>` are not touched.

## Customize

### Change the prefix key

Edit `~/.config/herdr/config.toml` directly:

```toml
[keys]
prefix = "ctrl+b"   # or "f12", "esc", etc.
```

`install.sh` only writes `prefix =` when none is already set; reruns preserve your choice.

### Add a new keybinding

Add it inside the `[keys]` section of `~/.config/herdr/config.toml`, but **outside** the `# >>> herdr-recipes managed: begin >>>` / `# <<< herdr-recipes managed: end <<<` block. Anything inside the marker block gets replaced on the next `./install.sh`.

### Change which agents hopen starts

Edit `scripts/hopen-agents.conf` and re-run `./install.sh` (or just save — `scripts/` is a symlink, so changes are picked up immediately).

### Disable a layout

Comment out the corresponding `[[keys.command]]` line in `config/keys.toml`. After `./install.sh`, that chord will be unbound.

## Layout on disk

```
<repo>/
├── VERSION                       ← version number (read by every script)
├── CHANGELOG.md
├── install.sh
├── config/keys.toml
├── config/agent-catalog.json
└── scripts/
    ├── version.sh                ← sourced by the shell scripts for -v
    ├── agent-audit.py
    ├── hopen.sh
    ├── hopen-once.sh
    ├── hopen-agents.conf
    └── herdr-pane-switch.py

~/.config/herdr/
├── config.toml                  ← modified by install.sh (managed block inside [keys])
└── scripts/                     → symlink to <repo>/scripts/
```

## License

MIT. See [LICENSE](LICENSE).


## Verification

```bash
python3 -m unittest discover -s tests -v
python3 tests/smoke_herdr_layouts.py --binary /path/to/herdr --output /tmp/herdr-smoke.json
python3 tests/smoke_hstatus.py --binary /path/to/herdr --output /tmp/hstatus-smoke.json
```

The manual smoke uses an isolated server and temporary configuration, starts no
agents, and checks both launchers across all nine layouts. Version 0.8.0 was
verified on macOS with Herdr 0.8.2 and 0.9.5; see
[the recorded evidence](docs/herdr-research.md).
