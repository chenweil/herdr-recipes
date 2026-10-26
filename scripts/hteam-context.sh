#!/usr/bin/env bash
# hteam-context.sh — build the shared conversational team context; no Herdr calls.
#
# Usage: bash hteam-context.sh -C PATH --workspace ID --tab ID --pane ID --kind KIND
#   --name NAME           optional live coordinator name (otherwise use its pane)
#   --config FILE         team JSON (default: PATH/.herdr-team.json, if present)
#   --worker-kind KIND    override the configured worker kind (default: coordinator)
#   --max-workers N       override the configured live-worker limit (default: 4)
#   -h, --help            show help
#   -v, -V, --version     show repository version
#
# IDs/kind must come from live Herdr reads. This helper validates configuration,
# prints JSON and creates no files, panes, agents or task queue. Source it from
# hteam.sh so new-team and in-place coordination use the same defaults.

HTEAM_CONTEXT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# Walk up from the requested directory looking for .herdr-team.json, matching how
# agents discover project .agents/skills. A linked worktree root carries its own
# .git file, so hop to the main worktree root instead of stopping there: a worker
# checkout must inherit the project's team policy.
_ht_find_config() {
  local target_dir="$1" dir common main_root
  dir=$(cd "$target_dir" 2>/dev/null && pwd -P) || return 0
  while :; do
    [ -f "$dir/.herdr-team.json" ] && { printf '%s\n' "$dir/.herdr-team.json"; return 0; }
    if [ -e "$dir/.git" ]; then
      if [ -f "$dir/.git" ] && common=$(git -C "$dir" rev-parse --git-common-dir 2>/dev/null); then
        case "$common" in /*) ;; *) common="$dir/$common" ;; esac
        main_root=$(cd "$common/.." 2>/dev/null && pwd -P) || return 0
        if [ -n "$main_root" ] && [ "$main_root" != "$dir" ]; then
          dir="$main_root"
          continue
        fi
      fi
      return 0
    fi
    [ "$dir" = "/" ] && return 0
    dir=$(dirname "$dir")
  done
}

_ht_load_policy() {
  local target_dir="$1" coordinator_kind="$2" config_path="$3" worker_override="$4" max_override="$5"
  local body='{}' worker max
  if [ -z "$config_path" ]; then
    config_path=$(_ht_find_config "$target_dir")
  fi
  HTEAM_CONFIG_PATH=""
  if [ -n "$config_path" ]; then
    if [ ! -f "$config_path" ] || [ ! -r "$config_path" ]; then
      echo "hteam: team config not readable: $config_path" >&2; return 2
    fi
    if ! body=$(jq -esc '
      if length == 1 and (.[0] | type == "object") then .[0] else error("expected one object") end
      | select((keys - ["worker_kind", "max_workers", "skills"]) | length == 0)
      | select(.worker_kind == null or (.worker_kind | type == "string" and test("^[a-z][a-z0-9_-]*$")))
      | select((has("max_workers") | not) or
          (.max_workers | type == "number" and . > 0 and . == floor))
      | select((has("skills") | not) or (
          (.skills | type) == "object"
          and (((.skills | keys) - ["implement", "review", "diagnose", "route"]) | length) == 0
          and ([.skills[] | type == "string" and test("^[a-z][a-z0-9_-]*$")] | all)
        ))
    ' "$config_path"); then
      echo "hteam: invalid team config: $config_path (worker_kind: kind or null; max_workers: positive integer; skills: object of implement/review/diagnose/route to skill name)" >&2
      return 2
    fi
    HTEAM_CONFIG_PATH="$(cd "$(dirname "$config_path")" && pwd -P)/$(basename "$config_path")"
  fi
  worker=$(jq -r --arg kind "$coordinator_kind" '.worker_kind // $kind' <<<"$body")
  max=$(jq -r '.max_workers // 4' <<<"$body")
  [ -z "$worker_override" ] || worker="$worker_override"
  [ -z "$max_override" ] || max="$max_override"
  worker=$(_resolve_kind "$worker")
  if ! [[ "$worker" =~ ^[a-z][a-z0-9_-]*$ ]] || ! [[ "$max" =~ ^[1-9][0-9]*$ ]]; then
    echo "hteam: worker kind must be a kind name; max-workers must be a positive integer" >&2; return 2
  fi
  HTEAM_POLICY=$(jq -n --arg kind "$worker" --argjson max "$max" --argjson skills "$(jq -c '.skills // {}' <<<"$body")" \
    '{worker_kind:$kind,max_workers:$max,skills:$skills}')
}

_ht_context() {
  local target_dir="$1" ws_id="$2" tab_id="$3" pane_id="$4" name="$5" kind="$6" team_id
  # Runtime-only identity, independent of IDs that can recur in another server.
  team_id="team-$(date +%Y%m%dT%H%M%S)-$$-$RANDOM"
  jq -n --arg ws "$ws_id" --arg tab "$tab_id" --arg pane "$pane_id" \
    --arg name "$name" --arg kind "$kind" --arg cwd "$target_dir" \
    --arg core "$HTEAM_CONTEXT_DIR/hopen.sh" --arg status "$HTEAM_CONTEXT_DIR/hstatus.sh" \
    --arg entry "$HTEAM_CONTEXT_DIR/hteam-context.sh" --arg config "$HTEAM_CONFIG_PATH" \
    --arg team "$team_id" --arg records "$target_dir/.herdr-recipes/teams/$team_id" \
    --argjson policy "$HTEAM_POLICY" \
    '{workspace_id:$ws,tab_id:$tab,coordinator_pane_id:$pane,
      coordinator_name:(if $name == "" then null else $name end),coordinator_kind:$kind,
      cwd:$cwd,launcher_core:$core,status_entry:$status,context_entry:$entry,
      team_id:$team,record_dir:$records,team_config:$policy,
      config_path:(if $config == "" then null else $config end)}'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  source "$HTEAM_CONTEXT_DIR/hopen.sh"
  target_dir="$PWD" ws_id="" tab_id="" pane_id="" name="" kind=""
  config_path="${HTEAM_CONFIG:-}" worker_override="" max_override=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -C|--path|--workspace|--tab|--pane|--name|--kind|--config|--worker-kind|--max-workers)
        if [ $# -lt 2 ] || [ -z "$2" ]; then echo "hteam-context: $1 requires a value" >&2; exit 2; fi
        case "$1" in
          -C|--path) target_dir="$2" ;; --workspace) ws_id="$2" ;; --tab) tab_id="$2" ;;
          --pane) pane_id="$2" ;; --name) name="$2" ;; --kind) kind="$2" ;;
          --config) config_path="$2" ;; --worker-kind) worker_override="$2" ;; --max-workers) max_override="$2" ;;
        esac
        shift 2 ;;
      -h|--help) awk 'NR > 1 && /^#/ { print; next } NR > 1 { exit }' "$0"; exit 0 ;;
      -v|-V|--version) _hr_print_version hteam-context; exit 0 ;;
      *) echo "hteam-context: unknown argument $1; see --help" >&2; exit 2 ;;
    esac
  done
  if [ -z "$ws_id" ] || [ -z "$tab_id" ] || [ -z "$pane_id" ] || [ -z "$kind" ]; then
    echo "hteam-context: --workspace, --tab, --pane and --kind require live context" >&2; exit 2
  fi
  target_dir="$(cd -- "$target_dir" && pwd -P)" || exit 2
  kind=$(_resolve_kind "$kind")
  _ht_load_policy "$target_dir" "$kind" "$config_path" "$worker_override" "$max_override"
  _ht_context "$target_dir" "$ws_id" "$tab_id" "$pane_id" "$name" "$kind"
fi
