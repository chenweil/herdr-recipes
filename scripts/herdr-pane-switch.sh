#!/bin/bash
# herdr-pane-switch: switch to pane by index (1-based)
# Usage: herdr-pane-switch 1..6
# Reads HERDR_SOCKET_PATH if set, otherwise uses default.
set -euo pipefail

IDX="${1:-1}"

# Find current workspace from herdr session
WS=$(herdr workspace get "$(herdr session current --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["workspace_id"])')" --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' 2>/dev/null || echo "")

if [ -z "$WS" ]; then
  # fallback: list panes in current session and pick the N-th
  herdr pane list --json 2>/dev/null | python3 -c "
import json, sys
panes = json.load(sys.stdin)
idx = int('$IDX') - 1
if 0 <= idx < len(panes):
    print(panes[idx]['id'])
else:
    print('', end='')
" | xargs -I{} herdr pane focus --pane {} 2>/dev/null || true
  exit 0
fi

# Get panes for this workspace, sorted by creation order
herdr pane list --workspace "$WS" --json 2>/dev/null | python3 -c "
import json, sys
panes = json.load(sys.stdin)
idx = int('$IDX') - 1
if 0 <= idx < len(panes):
    print(panes[idx]['id'])
else:
    print('', end='')
" | xargs -I{} herdr pane focus --pane {} 2>/dev/null || true
