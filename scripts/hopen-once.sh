#!/usr/bin/env bash
# hopen-once.sh — open a hopen layout with kinds specified as positional args.
# Bypasses hopen-agents.conf entirely.
#
# Usage:
#   hopen-once.sh KIND1 KIND2 KIND3 [KIND4]
#   hopen-once.sh --layout|-l CODE KIND1 KIND2 ...
#   hopen-once.sh --layout CODE --kind|-k K1 --prompt|-p P1 --kind K2 --prompt P2 ...
#   hopen-once.sh --no-agents|-n                  # build bare layout only
#   hopen-once.sh --path|-C PATH                  # cwd for the new workspace (default: .)
#   hopen-once.sh --pane-name|-N "A,B,C"         # comma-separated, equivalent to -N A -N B -N C
#   hopen-once.sh --version|-v|-V                 # print version and exit
#   hopen-once.sh --help|-h
#
# 重复同一 kind N 次：用 `K:N` 语法。`--kind codex:4` 等价于
# `-k codex -k codex -k codex -k codex`，但只打一次。
#
# Short flags: -l layout, -k kind, -p prompt, -N pane-name, -n no-agents, -C path, -v version, -h help.
# (-N 是大写 N，因为 -n 被 --no-agents 占了。)
#
# Layout auto-pick (when --layout omitted, based on N kinds):
#   2 kinds → 11 (side by side)
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
# Per-pane labels: --pane-name|-N matches KINDS[] by index (visual order).
# 缺省 = 位置名（left / left-top / right-bottom / middle 等）。
#   hopen-once.sh -l 22 \
#     -k codex -N "codex-A" -p "implement A" \
#     -k codex -N "codex-B" -p "implement B" \
#     -k pi   -N "review"   -p "review"
#   hopen-once.sh -l 22 -k codex:2 -k pi:2 -N codex-A -N codex-B -N pi-A -N pi-B
#
# 逗号分隔语法：-N "A,B,C" 等价于 -N A -N B -N C。名里本身含逗号需手动 escape。
#   hopen-once.sh -l 22 -k codex:4 -N "a,b,c,d"
#
# 位置参数溢出语法：KINDS[] 超过 layout pane 数时，溢出部分 prepend 到 NAMES，
# 跟在 KINDS 后面追加的位置参数当 pane name（按视觉顺序），不用 -N。
# NAMES[i] 对应第 i+1 个视觉位，不是 pane ID 顺序——21/31/22 的创建顺序跟视觉
# 顺序不一致，用 `herdr pane list` 核对时注意它按创建顺序返回。
#   hopen-once.sh -l 21 pi pi codex pi-top pi-bot cd-right
#     → pi-top = 视觉位 1 (left-top)，pi-bot = 2 (left-bottom)，cd-right = 3 (right)
#   hopen-once.sh -l 22 codex codex pi claude A B
#     → A = 视觉位 1 (left-top)，B = 2 (right-top)
#     → 视觉位 3、4 没给名字，回落成 left-bottom / right-bottom
#
# Examples:
#   hopen-once.sh codex pi                          # 2 panes (layout 11)
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

# 按视觉阅读顺序（行优先）输出每个 layout 的位置名。
# `hopen.sh` 的 pane 创建顺序是列优先（21/31/22 会反序），但用户看到的布局是
# 行优先的。用这个函数把用户传的 NAMES[] 跟 KINDS[] 反查回 created[] 顺序。
_h_row_major() {
  case "$1" in
    11)  printf 'left\nright\n' ;;
    12)  printf 'left\nright-top\nright-bottom\n' ;;
    21)  printf 'left-top\nleft-bottom\nright\n' ;;
    13)  printf 'left\nright-top\nright-mid\nright-bottom\n' ;;
    31)  printf 'left-top\nleft-mid\nleft-bottom\nright\n' ;;
    22)  printf 'left-top\nright-top\nleft-bottom\nright-bottom\n' ;;
    111) printf 'left\nmiddle\nright\n' ;;
  esac
}

# --- Parse hopen-once.sh flags ---
LAYOUT=""
NO_AGENTS=0
KINDS=()
PROMPTS=()
NAMES=()        # pane label 列表，跟 KINDS[] 同索引（视觉顺序）
PATH_ARG="."     # 默认当前目录；相对/绝对路径都可以

print_help() {
  sed -n '2,71p' "${BASH_SOURCE[0]}"
}

# 把 --path 解析成绝对路径：展开 ~，相对路径相对当前 $PWD 解析，最后 cd && pwd 规范化。
_resolve_path() {
  local p="$1"
  case "$p" in
    "~"|"~/") p="$HOME" ;;
    "~"/*)    p="$HOME/${p#\~/}" ;;
  esac
  # 先存回，交给 cd 规范化；目录不存在时 cd 会失败，保留原值给 herdr 报错
  if [ -d "$p" ]; then
    p="$(cd "$p" && pwd)" || true
  fi
  echo "$p"
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

# 把 "-N a,b,c" 里的逗号分隔值展开成多条 NAMES。跟 -N 重复 flag 等价：
#   -N "a,b,c"          = -N a -N b -N c
#   -N "a, b, c"        = -N "a" -N " b" -N " c"  （trim 头尾空白）
# 不含逗号就走原样追加，跟 _expand_kind 对称。
_expand_name() {
  local raw="$1"
  case "$raw" in
    *,*)
      local part
      # IFS=',' read -ra 是 bash 标准分割；分隔后逐个 trim 头尾空白。
      local IFS=','
      local -a _parts
      _parts=($raw)
      for part in "${_parts[@]}"; do
        # trim leading + trailing whitespace
        part="${part#"${part%%[![:space:]]*}"}"
        part="${part%"${part##*[![:space:]]}"}"
        NAMES+=("$part")
      done
      ;;
    *)
      NAMES+=("$raw")
      ;;
  esac
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-agents|-n) NO_AGENTS=1; shift ;;
    --kind|-k)       _expand_kind "$2" || exit 2; shift 2 ;;
    --prompt|-p)     PROMPTS+=("$2"); shift 2 ;;
    --pane-name|-N)  _expand_name "$2"; shift 2 ;;
    --layout|-l)     LAYOUT="$2"; shift 2 ;;
    --path|-C)       PATH_ARG="$2"; shift 2 ;;
    --version|-v|-V) _hr_print_version hopen-once; exit 0 ;;
    --help|-h)       print_help; exit 0 ;;
    --) shift; break ;;
    -*) echo "hopen-once: 未知选项 '$1'。支持: --no-agents/-n / --kind/-k / --prompt/-p / --pane-name/-N / --layout/-l / --path/-C / --version/-v / --help/-h" >&2; exit 2 ;;
    *)  _expand_kind "$1" || exit 2; shift ;;
  esac
done

# Resolve --path to absolute (supports ~, relative, absolute). Default '.'.
RESOLVED_PATH="$(_resolve_path "$PATH_ARG")" || exit 2

# --- Determine layout ---
if [ -z "$LAYOUT" ]; then
  case "${#KINDS[@]}" in
    2) LAYOUT=11 ;;
    3) LAYOUT=12 ;;
    4) LAYOUT=22 ;;
    *)
      echo "hopen-once: 没有 --layout，且 kind 数量 ${#KINDS[@]} 不支持自动布局（2 → 11，3 → 12，4 → 22）" >&2
      echo "             传 --layout CODE 显式指定，或调整 kind 数为 2、3 或 4" >&2
      echo "             支持: 11 12 21 22 13 31 111" >&2
      exit 2
      ;;
  esac
fi

# Validate layout (also gives a friendly error if user passes a typo)
if ! _steps_for "$LAYOUT" >/dev/null 2>&1; then
  echo "hopen-once: 未知布局 '$LAYOUT'。支持: 11 12 21 22 13 31 111" >&2
  exit 2
fi

# 把超出 max_panes 的 KINDS 溢出部分 prepend 到 NAMES。
# 顺序语义：位置参数里 KINDS 后面紧跟的值按视觉顺序在 NAMES 前面，-N flag 追加的在后面。
# 读全局 KINDS[]/NAMES[]；写全局 KINDS[]/NAMES[]。脚本顶层调用，不是函数。
_cap_kinds_to_panes() {
  local max_panes="$1"
  if [ "${#KINDS[@]}" -gt "$max_panes" ]; then
    local extra=("${KINDS[@]:$max_panes}")
    echo "hopen-once: kind 数 ${#KINDS[@]} 超过布局 $LAYOUT 的 pane 数 $max_panes" >&2
    echo "             多余部分当 NAMES: ${extra[*]}" >&2
    KINDS=("${KINDS[@]:0:$max_panes}")
    if [ "${#NAMES[@]}" -eq 0 ]; then
      NAMES=("${extra[@]}")
    else
      local old_names=("${NAMES[@]}")
      NAMES=("${extra[@]}" "${old_names[@]}")
    fi
  fi
}

# Cap kinds to layout pane count. _panes_for 在 hopen.sh 里定义，source 后可用。
max_panes=$(_panes_for "$LAYOUT")
_cap_kinds_to_panes "$max_panes"

# Cap names to layout pane count (与 KINDS 相同处理)。
if [ "${#NAMES[@]}" -gt "$max_panes" ]; then
  echo "hopen-once: name 数 ${#NAMES[@]} 超过布局 $LAYOUT 的 pane 数 $max_panes" >&2
  echo "             多余 name 被忽略: ${NAMES[*]:$max_panes}" >&2
  NAMES=("${NAMES[@]:0:$max_panes}")
fi

# --- Build layout (shared with hopen.sh via _h_build_layout) ---
#
# pane_names 按 created[] 顺序构造：NAMES[] 跟 KINDS[] 一样是视觉顺序（行优先），
# 需要先按 _position_for 反向映射到 created idx，再交给 _h_build_layout 重命名。
# NAMES[i] 为空的 pane 回退到位置名（_pane_name_for 同样的默认行为），保持跟
# hopen.sh 一致——两个入口都保证所有 pane 有可读默认名。
pane_names=""
created_names=()
i=0
while [ $i -lt "$max_panes" ]; do
  # 初始填位置名（默认）
  created_names+=("$(_position_for "$LAYOUT" "$i")")
  i=$((i+1))
done
# 遍历视觉位置，用 _position_for 反查 created idx 覆盖 NAMES[] 里的项
visual_idx=0
while IFS= read -r pos; do
  nm="${NAMES[$visual_idx]:-}"
  if [ -n "$nm" ]; then
    j=0
    while [ $j -lt "$max_panes" ]; do
      if [ "$(_position_for "$LAYOUT" "$j")" = "$pos" ]; then
        created_names[$j]="$nm"
        break
      fi
      j=$((j+1))
    done
  fi
  visual_idx=$((visual_idx+1))
done < <(_h_row_major "$LAYOUT")
# 转成换行分隔的字符串
for nm in "${created_names[@]}"; do
  pane_names+="${nm}"$'\n'
done

_h_build_layout "$LAYOUT" "" "$RESOLVED_PATH" "" "$pane_names" || { echo "hopen-once: 布局创建失败" >&2; exit 1; }

ws_id="${HOPEN_WS_ID}"
created=( "${HOPEN_CREATED_PANES[@]}" )

# --- Dispatch agents in row-major visual order ---
#
# `_position_for <layout> <created_idx>` returns the position name at that
# created pane idx (e.g. "left-top", "right-bottom"). We map user kinds to
# panes by iterating row-major position names and finding the created pane
# whose `_position_for` matches. This decouples user-facing order (visual
# reading) from hopen.sh's internal column-major creation order.
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
