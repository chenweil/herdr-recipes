#!/usr/bin/env bash
# hopen-once.sh — open a hopen layout with kinds specified as positional args.
# Bypasses hopen-agents.conf entirely.
#
# Usage:
#   hopen-once.sh KIND1 KIND2 KIND3 [KIND4]
#   hopen-once.sh --layout|-l CODE KIND1 KIND2 ...
#   hopen-once.sh --layout CODE --kind|-k K1 --prompt|-p P1 --kind K2 --prompt P2 ...
#   hopen-once.sh --no-agents|-n                  # build bare layout only
#   hopen-once.sh --help|-h
#
# 重复同一 kind N 次：用 `K:N` 语法。`--kind codex:4` 等价于
# `-k codex -k codex -k codex -k codex`，但只打一次。
#
# Short flags: -l layout, -k kind, -p prompt, -n no-agents, -h help.
#
# Layout auto-pick (when --layout omitted, based on N kinds):
#   3 kinds → 12 (left big + right column)
#   4 kinds → 22 (2x2)
#   other   → error (use --layout for non-default)
#
# Kinds are placed in panes in visual reading order
# (left-to-right, top-to-bottom). Use _resolve_kind aliases where helpful:
#   op → opencode, cc → claude, cd → codex, pi → pi.
#
# Per-pane prompts: --prompt P is matched to kinds by index. Missing
# entries default to empty. Use --kind and --prompt alternately for clarity:
#
#   hopen-once.sh -l 22 \
#     -k codex -p "implement A" \
#     -k codex -p "implement B" \
#     -k pi   -p "review"
#
# Examples:
#   hopen-once.sh codex codex pi                    # 3 panes (layout 12)
#   hopen-once.sh codex codex codex claude          # 4 panes (layout 22)
#   hopen-once.sh op op cd                          # aliases → 12 layout
#   hopen-once.sh -l 13 codex codex codex pi        # 4 panes (layout 13)
#   hopen-once.sh -n                                # bare ws in current cwd
#
# Compared to hopen.sh:
#   - Bypasses hopen-agents.conf entirely (no auto-dispatch)
#   - Layout auto-detected from kind count (or --layout override)
#   - Kinds placed in row-major visual order (see _h_row_major below)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source hopen.sh for primitives (_h_build_layout, _h_ws, _h_split, _steps_for,
# _position_for, _start_agent, _resolve_kind). After recent refactor, hopen.sh
# is sourceable: flag parsing lives inside hopen() and the top-level guard
# prevents it from running on source.
source "$SCRIPT_DIR/hopen.sh"

# --- Parse hopen-once.sh flags ---
LAYOUT=""
NO_AGENTS=0
KINDS=()
PROMPTS=()

print_help() {
  sed -n '2,28p' "${BASH_SOURCE[0]}"
}

# 把 "K" 或 "K:N" 展开成 1 或 N 条 KINDS。N 必须 1-9 开头的正整数。
# `-k codex:4` 等价于 `-k codex -k codex -k codex -k codex`，但只打一次。
_expand_kind() {
  local raw="$1"
  case "$raw" in
    *:*)
      local k="${raw%:*}" n="${raw#*:}"
      if [ -z "$k" ]; then
        echo "hopen-once: '$raw' 缺少 kind 部分" >&2; return 2
      fi
      if [[ "$n" =~ ^[1-9][0-9]*$ ]]; then
        local i=0
        while [ $i -lt "$n" ]; do
          KINDS+=("$k")
          i=$((i + 1))
        done
      else
        echo "hopen-once: '$raw' 的计数 '$n' 不是正整数" >&2; return 2
      fi
      ;;
    *)
      KINDS+=("$raw")
      ;;
  esac
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-agents|-n) NO_AGENTS=1; shift ;;
    --kind|-k)       _expand_kind "$2" || exit 2; shift 2 ;;
    --prompt|-p)     PROMPTS+=("$2"); shift 2 ;;
    --layout|-l)     LAYOUT="$2"; shift 2 ;;
    --help|-h)       print_help; exit 0 ;;
    --) shift; break ;;
    -*) echo "hopen-once: 未知选项 '$1'。支持: --no-agents/-n / --kind/-k / --prompt/-p / --layout/-l / --help/-h" >&2; exit 2 ;;
    *)  _expand_kind "$1" || exit 2; shift ;;
  esac
done

# --- Determine layout ---
if [ -z "$LAYOUT" ]; then
  case "${#KINDS[@]}" in
    3) LAYOUT=12 ;;
    4) LAYOUT=22 ;;
    *)
      echo "hopen-once: 没有 --layout，且 kind 数量 ${#KINDS[@]} 不支持自动布局（3 → 12，4 → 22）" >&2
      echo "             传 --layout CODE 显式指定，或调整 kind 数为 3 或 4" >&2
      echo "             支持: 12 21 22 13 31 111" >&2
      exit 2
      ;;
  esac
fi

# Validate layout (also gives a friendly error if user passes a typo)
if ! _steps_for "$LAYOUT" >/dev/null 2>&1; then
  echo "hopen-once: 未知布局 '$LAYOUT'。支持: 12 21 22 13 31 111" >&2
  exit 2
fi

# Cap kinds to layout pane count. _steps_for 输出 N 行 (N = split 次数),
# 总 pane 数 = N + 1 (含 root)。直接查表比运行 _steps_for 更可靠，
# 也避免 `$(( $(多行) + 1 ))` 被算术上下文当作变量查的陷阱。
_panes_for() {
  case "$1" in
    12|21|111) echo 3 ;;
    13|31|22)  echo 4 ;;
    *)         echo 0 ;;
  esac
}
max_panes=$(_panes_for "$LAYOUT")
if [ "${#KINDS[@]}" -gt "$max_panes" ]; then
  # Split into two echos to avoid bash 3.2 bug: 全角 ；+ 变量展开 + set -u
  # 在同一双引号里会误报变量未设。
  echo "hopen-once: kind 数 ${#KINDS[@]} 超过布局 $LAYOUT 的 pane 数 $max_panes" >&2
  echo "             多余 kind 被忽略: ${KINDS[*]:$max_panes}" >&2
  KINDS=("${KINDS[@]:0:$max_panes}")
fi

# --- Build layout (shared with hopen.sh via _h_build_layout) ---
_h_build_layout "$LAYOUT" || { echo "hopen-once: 布局创建失败" >&2; exit 1; }

ws_id="${HOPEN_WS_ID}"
created=( "${HOPEN_CREATED_PANES[@]}" )

# --- Dispatch agents in row-major visual order ---
#
# `_position_for <layout> <created_idx>` returns the position name at that
# created pane idx (e.g. "left-top", "right-bottom"). We map user kinds to
# panes by iterating row-major position names and finding the created pane
# whose `_position_for` matches. This decouples user-facing order (visual
# reading) from hopen.sh's internal column-major creation order.
_h_row_major() {
  case "$1" in
    12)  printf 'left\nright-top\nright-bottom\n' ;;
    21)  printf 'left-top\nleft-bottom\nright\n' ;;
    13)  printf 'left\nright-top\nright-mid\nright-bottom\n' ;;
    31)  printf 'left-top\nleft-mid\nleft-bottom\nright\n' ;;
    22)  printf 'left-top\nright-top\nleft-bottom\nright-bottom\n' ;;
    111) printf 'left\nmiddle\nright\n' ;;
  esac
}

if [ "$NO_AGENTS" -eq 0 ] && [ "${#KINDS[@]}" -gt 0 ]; then
  i=0
  while IFS= read -r pos; do
    user_kind="${KINDS[$i]:-}"
    [ -n "$user_kind" ] || { i=$((i+1)); continue; }
    user_prompt="${PROMPTS[$i]:-}"

    # Find the created pane whose _position_for matches this row-major pos
    target_pane=""
    for j in "${!created[@]}"; do
      if [ "$(_position_for "$LAYOUT" "$j")" = "$pos" ]; then
        target_pane="${created[$j]}"
        break
      fi
    done
    if [ -z "$target_pane" ]; then
      echo "[hopen-once] 跳过 $user_kind: 找不到对应 pane ($pos)" >&2
      i=$((i+1)); continue
    fi

    _start_agent "hopen-once-${LAYOUT}-${pos}" "$target_pane" "$user_kind" "$user_prompt"
    i=$((i+1))
  done < <(_h_row_major "$LAYOUT")
fi

# --- Focus new workspace + emit result ---
herdr workspace focus "$ws_id" >/dev/null 2>&1 || true

echo "ws=$ws_id panes=${created[*]}"
