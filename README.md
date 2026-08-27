# herdr-recipes

Personal recipes for [herdr](https://herdr.dev) — key bindings, layout presets, and helper scripts, packaged for one-command install on any machine.

## What's in here

| File | Purpose |
|---|---|
| `scripts/hopen.sh` | Open a numbered-pane layout (`12`, `21`, `22`, `13`, `31`, `111`) in a new workspace, optionally dispatching agents from `hopen-agents.conf` |
| `scripts/hopen-agents.conf` | Per-layout / per-pane agent dispatch (which pane gets `claude` / `codex` / `pi` / …) |
| `scripts/herdr-pane-switch.py` | Switch to the N-th pane (1-based) in the active workspace |
| `scripts/herdr-pane-switch.sh` | Same as above, bash + python helper for machines without standalone python |
| `scripts/README.md` | Detailed docs for the hopen / hopen-agents scripts |
| `config/keys.toml` | All `[[keys.command]]` entries this repo manages |
| `install.sh` | One-shot installer / upgrader / uninstaller |

## Key bindings (with default `cmd+b` prefix)

| Chord | Action |
|---|---|
| `prefix 1..6` | Switch to pane N in active workspace |
| `prefix alt 1..6` | Open hopen layout (auto-start agents) — `1=12, 2=21, 3=22, 4=13, 5=31, 6=111` |
| `prefix ctrl 1..6` | Open hopen layout (bare panes, no agents) — same codes |

See `scripts/README.md` for layout code → visual layout mapping.

## Install (one-time, per machine)

Prerequisites: `herdr` already installed, `python3` in PATH (used by `herdr-pane-switch.py` and `install.sh`).

```bash
git clone https://github.com/chenweil/herdr-recipes.git ~/playground/herdr-recipes
cd ~/playground/herdr-recipes
./install.sh
```

`install.sh` does the following, in order:

1. Symlinks `~/.config/herdr/scripts/` → `<repo>/scripts/`. If a `scripts/` directory already exists, it's renamed to `scripts.bak.<timestamp>` first.
2. Removes any pre-existing managed block (delimited by `# >>> herdr-recipes managed: begin >>>` / `# <<< ... end <<<`) from `~/.config/herdr/config.toml`.
3. Removes any legacy `[[keys.command]]` blocks matching the patterns this repo manages (`prefix+1..6`, `prefix+(alt|ctrl)+1..6`). This makes upgrading from a manual setup clean.
4. Bootstraps `prefix = "cmd+b"` inside the `[keys]` section if no `prefix =` line is already set. Override the default via `PREFIX_DEFAULT=ctrl+b ./install.sh`.
5. Inserts the managed block (the contents of `config/keys.toml`) inside the `[keys]` section, immediately before the next top-level section (`[experimental]`, `[ui]`, `[theme]`, …).
6. Runs `herdr config check`, then `herdr server reload-config`.

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

This removes the managed key block, unlinks `scripts/` (only if it still points at this repo), and reloads the herdr config. Files previously backed up to `scripts.bak.<timestamp>` are not touched.

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
~/.config/herdr/
├── config.toml                  ← modified by install.sh (managed block inside [keys])
└── scripts/                     → symlink to <repo>/scripts/
```

## License

MIT. See [LICENSE](LICENSE).
