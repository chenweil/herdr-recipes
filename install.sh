#!/usr/bin/env bash
# install.sh — install/upgrade herdr-recipes on this machine.
#
# Idempotent. Re-running replaces the managed key block in
# ~/.config/herdr/config.toml (other config untouched) and re-links
# scripts/ to the repo.
#
# Layout after install:
#   ~/.config/herdr/scripts/   → <repo>/scripts/   (symlink)
#   ~/.config/herdr/config.toml (managed key block inside [keys])
#
# Usage:
#   ./install.sh              install or upgrade
#   ./install.sh --uninstall  remove managed block and unlink scripts/
#
# Env vars:
#   HERDR_HOME      override herdr config dir (default: ~/.config/herdr)
#   PREFIX_DEFAULT  default prefix to bootstrap (default: "cmd+b")

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERDR_HOME="${HERDR_HOME:-$HOME/.config/herdr}"
SCRIPTS_SRC="$REPO_DIR/scripts"
SCRIPTS_DST="$HERDR_HOME/scripts"
CONFIG_SRC="$REPO_DIR/config/keys.toml"
CONFIG_DST="$HERDR_HOME/config.toml"

MARKER_BEGIN="# >>> herdr-recipes managed: begin >>>"
MARKER_END="# <<< herdr-recipes managed: end <<<"

PREFIX_DEFAULT="${PREFIX_DEFAULT:-cmd+b}"

say()  { printf '\033[1;34m[herdr-recipes]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[herdr-recipes]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[herdr-recipes]\033[0m %s\n' "$*" >&2; exit 1; }

# ── Parse flags ────────────────────────────────────────────────────
ACTION="install"
while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall|-u) ACTION="uninstall"; shift ;;
    --help|-h)
      sed -n '2,21p' "$0"
      exit 0
      ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
done

# ── Sanity checks ──────────────────────────────────────────────────
[ -d "$HERDR_HOME" ] || die "herdr config dir not found: $HERDR_HOME (install herdr first)"
[ -f "$CONFIG_DST" ] || die "herdr config not found: $CONFIG_DST"
[ -d "$SCRIPTS_SRC" ] || die "scripts/ missing in repo at $SCRIPTS_SRC"
[ -f "$CONFIG_SRC" ]  || die "keys.toml missing in repo at $CONFIG_SRC"
command -v python3 >/dev/null 2>&1 || die "python3 not found in PATH"

# ── Uninstall path ─────────────────────────────────────────────────
if [ "$ACTION" = "uninstall" ]; then
  say "uninstalling"
  if [ -L "$SCRIPTS_DST" ] && [ "$(readlink "$SCRIPTS_DST")" = "$SCRIPTS_SRC" ]; then
    rm "$SCRIPTS_DST"
    say "removed scripts/ symlink"
  else
    warn "scripts/ is not our symlink — leaving it alone"
  fi
  python3 - "$CONFIG_DST" "$MARKER_BEGIN" "$MARKER_END" <<'PYEOF'
import re, sys, pathlib
path, begin, end = sys.argv[1], sys.argv[2], sys.argv[3]
text = pathlib.Path(path).read_text()
new = re.sub(rf"{re.escape(begin)}[\s\S]*?{re.escape(end)}\n?", "", text)
if new != text:
    pathlib.Path(path).write_text(new)
    print(f"[herdr-recipes] stripped managed block from {path}")
else:
    print(f"[herdr-recipes] no managed block found in {path}")
PYEOF
  if command -v herdr >/dev/null 2>&1; then
    herdr server reload-config >/dev/null 2>&1 && say "herdr server reload-config: ok" \
      || warn "herdr server reload-config failed (is the server running?)"
  fi
  say "done"
  exit 0
fi

# ── Step 1: link scripts/ ──────────────────────────────────────────
if [ -L "$SCRIPTS_DST" ]; then
  target="$(readlink "$SCRIPTS_DST")"
  if [ "$target" = "$SCRIPTS_SRC" ]; then
    say "scripts/ already linked → $SCRIPTS_SRC"
  else
    warn "scripts/ symlink → $target; re-linking to $SCRIPTS_SRC"
    rm "$SCRIPTS_DST"
    ln -s "$SCRIPTS_SRC" "$SCRIPTS_DST"
  fi
elif [ -d "$SCRIPTS_DST" ]; then
  backup="${SCRIPTS_DST}.bak.$(date +%Y%m%d%H%M%S)"
  warn "backing up existing scripts/ → $backup"
  mv "$SCRIPTS_DST" "$backup"
  ln -s "$SCRIPTS_SRC" "$SCRIPTS_DST"
  say "linked scripts/ → $SCRIPTS_SRC"
else
  ln -s "$SCRIPTS_SRC" "$SCRIPTS_DST"
  say "linked scripts/ → $SCRIPTS_SRC"
fi

# ── Step 2: merge keys.toml into config.toml ───────────────────────
# All block-level edits done via inline python so we don't need awk state
# machines. Operations, in order:
#   1. strip any existing managed marker block (upgrade path)
#   2. strip legacy [[keys.command]] blocks whose key matches our patterns
#   3. bootstrap prefix = "<default>" inside [keys] if none present
#   4. insert managed block inside [keys], before any other top-level section
python3 - "$CONFIG_DST" "$CONFIG_SRC" "$MARKER_BEGIN" "$MARKER_END" "$PREFIX_DEFAULT" <<'PYEOF'
import re, sys, pathlib
cfg, src, begin, end, default_prefix = sys.argv[1:6]

cfg_text = pathlib.Path(cfg).read_text()
key_text = pathlib.Path(src).read_text().rstrip() + "\n"

# 1. Strip existing managed marker block
managed_pat = re.compile(rf"{re.escape(begin)}[\s\S]*?{re.escape(end)}\n?", re.MULTILINE)
cfg_text = managed_pat.sub("", cfg_text)

# 2. Strip legacy [[keys.command]] blocks matching our patterns
#    (handles upgrades from manual setups that pre-date this repo).
#    A block ends at the next line starting with `[` (next section or array header).
legacy_keys = [r"^prefix\+[1-6]$", r"^prefix\+(alt|ctrl)\+[1-6]$"]
block_pat = re.compile(r"^(\[\[keys\.command\]\][\s\S]*?)(?=^\[|\Z)", re.MULTILINE)
def legacy_filter(m):
    block = m.group(1)
    km = re.search(r'^\s*key\s*=\s*"([^"]+)"', block, re.MULTILINE)
    if not km: return block
    key = km.group(1)
    if any(re.match(p, key) for p in legacy_keys):
        return ""
    return block
cfg_text = block_pat.sub(legacy_filter, cfg_text)

# 3. Find [keys] section, bootstrap prefix if missing, find insertion point
lines = cfg_text.splitlines(keepends=True)
keys_idx = None
for i, ln in enumerate(lines):
    if re.match(r"^\[keys\](?:\s|#|$)", ln):
        keys_idx = i
        break

if keys_idx is None:
    # No [keys] section — create one at end of file
    cfg_text = cfg_text.rstrip() + f"\n\n[keys]\nprefix = \"{default_prefix}\"\n"
    lines = cfg_text.splitlines(keepends=True)
    keys_idx = len(lines) - 3

# Check whether prefix = is already set inside [keys] section
# (scan forward until next top-level [section])
section_end = len(lines)
for i in range(keys_idx + 1, len(lines)):
    if re.match(r"^\[(?!\[)", lines[i]):
        section_end = i
        break

has_prefix = any(re.match(r"^\s*prefix\s*=", lines[i]) for i in range(keys_idx, section_end))
if not has_prefix:
    # Insert prefix = line right after [keys] header
    lines.insert(keys_idx + 1, f'prefix = "{default_prefix}"\n')
    section_end += 1

# 4. Find insertion point: right before the next top-level section that
#    is NOT [[keys.command]]
insert_at = section_end
for i in range(section_end, len(lines)):
    if re.match(r"^\[(?!\[keys\.command\])", lines[i]):
        insert_at = i
        break

managed = f"\n{begin}\n{key_text}{end}\n"
new_lines = lines[:insert_at] + [managed] + lines[insert_at:]
pathlib.Path(cfg).write_text("".join(new_lines))
print(f"[herdr-recipes] merged keys.toml into {cfg}")
PYEOF

# ── Step 3: validate + reload ──────────────────────────────────────
if command -v herdr >/dev/null 2>&1; then
  if herdr config check >/dev/null 2>&1; then
    say "herdr config check: ok"
  else
    warn "herdr config check failed; review $CONFIG_DST manually:"
    herdr config check || true
  fi
  if herdr server reload-config >/dev/null 2>&1; then
    say "herdr server reload-config: ok"
  else
    warn "herdr server reload-config failed (is the server running?)"
  fi
else
  warn "'herdr' not found in PATH — skipped validation/reload"
fi

say "done"
say "  scripts/  → $SCRIPTS_DST (→ $SCRIPTS_SRC)"
say "  keys.toml → managed block in [keys] section of $CONFIG_DST"
