#!/usr/bin/env bash
# hteam.sh — start one conversational coordinator, then delegate through its pane.
#
# Usage:
#   hteam.sh                              # start a coordinator and wait for your request
#   hteam.sh -p "先准备两个 codex 和一个 pi，暂时不要工作"
#   hteam.sh -C /path/to/project -p "拆分任务并安排实现和审查"
#   hteam.sh --kind pi                     # default coordinator kind: codex
#
# Options:
#   -k, --kind KIND       coordinator agent kind (aliases supported)
#   -C, --path PATH       working directory (default: current directory)
#   -p, --prompt TEXT     optional first request to the coordinator
#       --no-focus        keep the user's current workspace focused
#   -v, -V, --version     print the shared repository version
#   -h, --help            show this help
#
# Only one pane/agent is bootstrapped. Workers are created/reused by the coordinator
# in response to your conversation; no background queue, retry or recovery engine.
# Startup/prompt failure retains the workspace for inspection and exits nonzero.

set -euo pipefail
TEAM_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$TEAM_SCRIPT_DIR/hopen.sh"

kind="codex" path_arg="$PWD" first_request="" no_focus=0
while [ $# -gt 0 ]; do
  case "$1" in
    --kind|-k|--path|-C|--prompt|-p)
      if [ $# -lt 2 ]; then
        echo "hteam: $1 requires a value" >&2; exit 2
      fi
      case "$1" in
        --kind|-k) kind="$2" ;;
        --path|-C) path_arg="$2" ;;
        --prompt|-p) first_request="$2" ;;
      esac
      shift 2 ;;
    --no-focus) no_focus=1; shift ;;
    --version|-v|-V) _hr_print_version hteam; exit 0 ;;
    --help|-h)
      awk 'NR > 1 && /^#/ { print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"
      exit 0 ;;
    *) echo "hteam: unknown argument '$1'; see --help" >&2; exit 2 ;;
  esac
done

[ -n "$kind" ] || { echo "hteam: kind must not be empty" >&2; exit 2; }
target_dir="$(cd -- "$path_arg" 2>/dev/null && pwd -P)" || {
  echo "hteam: directory not found: $path_arg" >&2; exit 2
}
for dependency in herdr jq; do
  command -v "$dependency" >/dev/null 2>&1 || { echo "hteam: $dependency not found" >&2; exit 1; }
done
template="$TEAM_SCRIPT_DIR/coordinator-prompt.md"
[ -r "$template" ] || { echo "hteam: coordinator prompt missing: $template" >&2; exit 1; }

label="$(_label_for "$target_dir")-team"
ws_label="$(_uniquify_label "$label" workspace)"
tab_label="$(_uniquify_label "$label" tab)"
if ! created=$(herdr workspace create --cwd "$target_dir" --label "$ws_label" --no-focus 2>&1); then
  printf 'hteam: workspace create failed: %s\n' "$created" >&2; exit 1
fi
if ! fields=$(jq -er '[.result.workspace.workspace_id, .result.root_pane.pane_id, .result.tab.tab_id]
  | select(all(.[]; type == "string" and length > 0)) | @tsv' <<<"$created"); then
  echo "hteam: workspace create returned invalid IDs; no agent was started" >&2; exit 1
fi
IFS=$'\t' read -r ws_id root_pane tab_id <<<"$fields"
name="$(_agent_name "$ws_id" coordinator)"
herdr tab rename "$tab_id" "$tab_label" >/dev/null 2>&1 || true
herdr pane rename "$root_pane" coordinator >/dev/null 2>&1 || true

context=$(jq -n --arg ws "$ws_id" --arg tab "$tab_id" --arg pane "$root_pane" \
  --arg name "$name" --arg cwd "$target_dir" --arg core "$TEAM_SCRIPT_DIR/hopen.sh" \
  --arg status "$TEAM_SCRIPT_DIR/hstatus.sh" \
  '{workspace_id:$ws, tab_id:$tab, coordinator_pane_id:$pane, coordinator_name:$name,
    cwd:$cwd, launcher_core:$core, status_entry:$status}')
prompt="$(cat "$template")"$'\n\n'
prompt+="## Runtime context (authoritative scope)"$'\n'
prompt+="$context"$'\n\n'
prompt+="## User's first request"$'\n'
if [ -n "$first_request" ]; then
  prompt+="$first_request"
else
  prompt+="尚无工作任务。简短说明你可以准备 agent 或接收任务，然后等待用户；不要自行创建 worker 或修改项目。"
fi

_reset_dispatch_state
_start_agent "$name" "$root_pane" "$kind" "$prompt"
_notify_dispatch
if [ "$no_focus" -eq 0 ]; then
  herdr workspace focus "$ws_id" >/dev/null 2>&1 || echo "hteam: could not focus workspace=$ws_id" >&2
fi
printf 'ws=%s panes=%s coordinator=%s\n' "$ws_id" "$root_pane" "$name"
if [ "$HOPEN_DISPATCH_FAILED" -ne 0 ]; then
  echo "hteam: coordinator setup incomplete; workspace=$ws_id retained for inspection" >&2
  exit 1
fi
