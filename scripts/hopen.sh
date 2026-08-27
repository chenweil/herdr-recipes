#!/usr/bin/env bash
# hopen.sh — 按代号打开 herdr pane 布局，并按 conf 启动 agent
#
# 用法：  bash hopen.sh <12|13|21|31|22|111> [--no-agents|-n]
#   --no-agents, -n   开布局但跳过 hopen-agents.conf，不启动任何 agent
#
# 代号（按"左列 + 右列"展开）：
#   12   左 1 + 右 2    [A][B / C]
#   21   右 1 + 左 2    [A / B][C]
#   13   左 1 + 右 3    [A][B / C / D]
#   31   右 1 + 左 3    [A / B / C][D]
#   22   2x2            [A / B][C / D]
#   111  三列各一        [A][B][C]
#
# Agent 启动：hopen.sh 跑完布局后读 hopen-agents.conf，按 section 启动 agent。
#   conf 缺失 / section 缺失 / kind 写错 / pane 没在 conf 里 → 跳过；
#   agent 启动失败会显示诊断，但不影响布局和其它 pane
#   详细 conf 格式见同目录 README.md。
#
# 设计原则：
#   1. 不硬编码 pane ID —— 全从 herdr 命令返回值取
#   2. 失败回滚 —— 任一 split 失败立刻关掉已开 pane + 关掉 ws
#   3. 不影响当前 ws —— 脚本开新 ws，所以 `prefix+alt+1..6` 在任何 ws 按都一样
#   4. agent 启动失败不影响布局 —— pane 留干净 shell 给用户
#   5. 命令对 herdr 0.8.0 --help 实测；布局数量对 split 实测
#   6. 成功创建的 ws 不由脚本自动清理；只有 split 失败时才走回滚，正常关闭由用户负责

set -euo pipefail

HOPEN_CONF="${HOPEN_CONF:-$HOME/.config/herdr/scripts/hopen-agents.conf}"

# Short aliases for agent kinds. User convenience — typing `op` instead
# of `opencode` in hopen-agents.conf or hopen-once.sh. Edit freely.
# Unknown aliases pass through unchanged so this stays forward-compatible
# with future herdr kinds.
# Implemented as a case statement (not associative array) for bash 3.2
# compatibility — macOS ships bash 3.2 as /bin/bash.

# 控制是否跳过 HOPEN_CONF 的 agent 派位。脚本顶层在调用 hopen() 前解析，
# hopen() 内部据此决定是否进入派位循环。设为 1 = 只开布局。
# 保留为全局默认值；hopen() 函数内用同名 local 覆盖。hopen-once.sh 不读它。
NO_AGENTS=0

# _h_build_layout / hopen() 的共享状态：ws 创建 + 所有 pane split 完后填充。
# 调用 _h_build_layout 后读 HOPEN_WS_ID 和 HOPEN_CREATED_PANES 即可拿到结果。
HOPEN_WS_ID=""
HOPEN_CREATED_PANES=()

_h_ws() {
  local out
  out=$(herdr workspace create --cwd "${2:-$PWD}" --label "$1" --no-focus) || return 1
  jq -r '"\(.result.workspace.workspace_id) \(.result.root_pane.pane_id) \(.result.tab.tab_id)"' <<<"$out"
}

_h_split() {
  herdr pane split "$1" --direction "$2" --no-focus \
    | jq -r '.result.pane.pane_id'
}

# 从 path 推导 ws 标签：git repo 用分支名，否则用 basename（最后一层目录名）。
# workspace 和 tab 都用这个标签，这样开多个 repo 的 ws 时在 tab bar 一眼能区分。
# 不可读字符（实际 git 分支名不会出现这种情况，但保底）不特殊处理，jq 会原样回传。
_label_for() {
  local path="$1"
  local branch
  # git -C path rev-parse 在不是 repo / git 未装 / 无 HEAD 时返回非零
  branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null) || branch=""
  if [ -n "$branch" ] && [ "$branch" != "HEAD" ]; then
    printf '%s' "$branch"
  else
    basename "$path"
  fi
}

# 给 label 加唯一后缀，避免跟 herdr 里现存的其他同名 ws / tab 撞名。
# 撞上就依次试 label+a / label+b / ... / label+z；都不够用则带时间戳。
# kind = "workspace" 或 "tab"；影响拉哪个列表做去重检查。
_uniquify_label() {
  local label="$1"
  local kind="$2"
  local existing new
  if [ "$kind" = "tab" ]; then
    existing=$(herdr tab list 2>/dev/null \
      | jq -r '.result.tabs[].label' 2>/dev/null) || existing=""
  else
    existing=$(herdr workspace list 2>/dev/null \
      | jq -r '.result.workspaces[].label' 2>/dev/null) || existing=""
  fi
  # 本身不撞 → 原样返回
  if ! printf '%s\n' "$existing" | grep -qxF "$label"; then
    printf '%s' "$label"; return
  fi
  # 依次试 +a / +b / ...
  local s
  for s in a b c d e f g h i j k l m n o p q r s t u v w x y z; do
    new="${label}+${s}"
    if ! printf '%s\n' "$existing" | grep -qxF "$new"; then
      printf '%s' "$new"; return
    fi
  done
  # 都满了（26 个）走 timestamp；不要年月日，仅取秒为避免太长
  printf '%s+%s' "$label" "$(date +%s)"
}

# 创建新 ws 并按 layout 代号完成所有 split。结果写全局 HOPEN_WS_ID 和
# HOPEN_CREATED_PANES（按创建顺序，root 在 idx=0）。任何一步 split 失败会
# 回滚已开 pane + 关 ws，函数返回非 0。
#
# 这是 hopen() 和 hopen-once.sh 共用的底层原语——避免重复实现 ws+split 流程。
_h_build_layout() {
  local code="$1"
  local ws_label="${2:-}"
  local cwd="${3:-$PWD}"   # 新 ws 的根 pane cwd；split 会继承，无需逐个传
  local tab_label="${4:-}" # tab 标题；空 = 跟随 ws_label
  local pane_names="${5:-}" # 换行分隔的 pane 标签列表，按 created[] 顺序；空行 = 该 pane 保持默认名

  # 默认 ws_label：从 cwd 推导（git repo → 分支名，否则 → basename）。
  # 调用方传了 $2 时（hopen() 默认参数的场景）保留调用方的值。
  [ -z "$ws_label" ] && ws_label=$(_label_for "$cwd")

  # tab_label 默认跟 ws_label；这样不传 -T 时 ws / tab 名字一致。
  [ -z "$tab_label" ] && tab_label="$ws_label"

  # 现有 herdr 里可能已有同名 ws / tab。加唯一后缀避免 tab bar 里区分不出。
  ws_label=$(_uniquify_label "$ws_label" workspace)
  tab_label=$(_uniquify_label "$tab_label" tab)

  local steps
  steps=$(_steps_for "$code") || return 2

  local ws_id root_pane tab_id
  # 读 3 个字段：ws_id、root_pane、tab_id（tab 创建后 herdr 自动命名为数字序号，
  # 所以后面还要 rename）
  read -r ws_id root_pane tab_id < <(_h_ws "$ws_label" "$cwd") || {
    echo "hopen: ws 创建失败" >&2
    return 1
  }

  # 把 tab 也 rename 成同 tab_label。这样 ws 列表和 tab bar 显示一致，
  # 多个项目同时开着的时候一眼能分清。
  herdr tab rename "$tab_id" "$tab_label" >/dev/null 2>&1 || true

  local created=("$root_pane") parent="$root_pane" pid parent_tok direction line
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    parent_tok="${line%% *}"
    direction="${line#* }"
    case "$parent_tok" in
      ROOT) parent="$root_pane" ;;
      LAST) parent="$pid"      ;;
      *) echo "hopen: 内部错 parent_tok=$parent_tok" >&2; return 1 ;;
    esac
    pid=$(_h_split "$parent" "$direction") || {
      echo "hopen: split 失败，开始回滚" >&2
      local p
      for p in "${created[@]:-}"; do herdr pane close "$p" >/dev/null 2>&1 || true; done
      herdr workspace close "$ws_id" >/dev/null 2>&1 || true
      return 1
    }
    created+=("$pid")
  done <<<"$steps"

  HOPEN_WS_ID="$ws_id"
  HOPEN_CREATED_PANES=("${created[@]}")

  # 按调用方传入的 pane_names 重命名 pane（按 created[] 顺序）。
  # 空行 = 该 pane 不改名（保持 herdr 默认名）。
  # rename 失败不阻断脚本，只把诊断写到 stderr —— pane 留可读默认名不影响布局。
  if [ -n "$pane_names" ]; then
    local i=0 name total=${#created[@]}
    while IFS= read -r name; do
      [ $i -ge "$total" ] && break
      if [ -n "$name" ]; then
        if ! herdr pane rename "${created[$i]}" "$name" >/dev/null 2>&1; then
          echo "[hopen] pane rename 失败: ${created[$i]} ← '$name'" >&2
        fi
      fi
      i=$((i+1))
    done <<<"$pane_names"
  fi

  return 0
}

# layout 代号 -> step 列表
# 每步格式：<parent_token> <direction>
#   parent_token: ROOT  (从 root 分裂)
#                 LAST  (从上一个创建的 pane 分裂)
#   direction:    right / down
#
# 12: [A][B/C]            -> ROOT right (B), LAST down (C)
# 21: [A/B][C]            -> ROOT right (C), ROOT down (B)   ← 镜像，先抢右列
# 13: [A][B/C/D]          -> ROOT right (B), LAST down (C), LAST down (D)
# 31: [A/B/C][D]          -> ROOT right (D), ROOT down (B), LAST down (C)
# 22: [A/B][C/D]          -> ROOT right (C), LAST down (D), ROOT down (B)
# 111: [A][B][C]          -> ROOT right (B), LAST right (C)
_steps_for() {
  case "$1" in
    12)  printf 'ROOT right\nLAST down\n' ;;
    21)  printf 'ROOT right\nROOT down\n' ;;
    13)  printf 'ROOT right\nLAST down\nLAST down\n' ;;
    31)  printf 'ROOT right\nROOT down\nLAST down\n' ;;
    22)  printf 'ROOT right\nLAST down\nROOT down\n' ;;
    111) printf 'ROOT right\nLAST right\n' ;;
    *)
      echo "hopen: 未知布局 '$1'。支持: 12 13 21 31 22 111" >&2
      return 2 ;;
  esac
}

# layout 代号 -> 按"位置"映射 pane 索引到 conf 里的 section 名
# `_position_for` 的 idx 是 `created` 数组索引，不是视觉顺序：
#   idx=0 永远是 workspace create 返回的 root pane；
#   idx=1+ 按 `_steps_for` 输出的顺序接收 pane split 返回值。
# 因此 idx 顺序 = `_steps_for` 顺序 = 实际 pane 创建顺序。
# 例如：
#   21: ROOT right 先创建 idx=1 的 right，ROOT down 再创建 idx=2 的 left-bottom；
#   22: ROOT right 创建 right-top，LAST down 创建 right-bottom，ROOT down 创建 left-bottom。
# 完整的创建 idx → 视觉位置顺序是：
#   12: left / right-top / right-bottom
#   21: left-top / right / left-bottom
#   13: left / right-top / right-mid / right-bottom
#   31: left-top / right / left-mid / left-bottom
#   22: left-top / right-top / right-bottom / left-bottom
#   111: left / middle / right
# section 名仍然是给 conf 使用的人类视觉位置
#   12: [A left][B right-top][C right-bottom]
#   21: [A left-top][B left-bottom][C right]
#   13: [A left][B right-top][C right-mid][D right-bottom]
#   31: [A left-top][B left-mid][C left-bottom][D right]
#   22: [A left-top][B left-bottom][C right-top][D right-bottom]
#   111:[A left][B middle][C right]
#
# 用法：_position_for <layout> <pane_index_0based>
_position_for() {
  local code="$1" idx="$2"
  case "$code:$idx" in
    12:0) echo left           ;; 12:1) echo right-top        ;; 12:2) echo right-bottom ;;
    21:0) echo left-top       ;; 21:1) echo right             ;; 21:2) echo left-bottom  ;;
    13:0) echo left           ;; 13:1) echo right-top        ;;
    13:2) echo right-mid      ;; 13:3) echo right-bottom     ;;
    31:0) echo left-top       ;; 31:1) echo right             ;;
    31:2) echo left-mid       ;; 31:3) echo left-bottom       ;;
    22:0) echo left-top       ;; 22:1) echo right-top         ;;
    22:2) echo right-bottom   ;; 22:3) echo left-bottom        ;;
    111:0) echo left          ;; 111:1) echo middle          ;; 111:2) echo right        ;;
    *) echo "pos-idx-$idx" ;;  # 兜底：未知 layout 时用索引本身当名字
  esac
}

# 读 conf：layout.LAYOUT.panes.POS.kind
# 返回：echo 出 kind 字符串，没配则 echo 空
#
# 解析策略：conf 格式是固定的 `[layout.XX.panes.Y]`（裸标识符，无引号） + `key = "val"`。
# 这不是通用 TOML 解析器，具体假设是：
#   - section header 必须从第 1 列开始，且精确匹配上述格式；
#   - kind/prompt 必须是单行、从第 1 列开始的双引号字符串；不处理多行字符串、
#     TOML 转义或值中的双引号；
#   - 用下一个从第 1 列开始的 `[` header 作为 section 边界，只取边界前第一个匹配键；
#     section 在文件中的全局顺序不敏感，普通字段在 kind/prompt 前面也不会影响匹配；
#     但嵌套表 header 会提前结束搜索范围。
# 直接 grep + sed 解析，避开 tomlq 依赖；扩展 conf 格式必须同步修改这里。
_kind_for() {
  local layout="$1" pos="$2"
  [ ! -f "$HOPEN_CONF" ] && return 0
  # 在匹配 section 之后取下一个 kind 行
  # set -e + pipefail 下 grep 无匹配会退出 1 → 必须 || true 包住
  sed -n "/^\[layout\.${layout}\.panes\.${pos}\]\$/,/^\[/p" "$HOPEN_CONF" 2>/dev/null \
    | { grep -m1 '^kind[[:space:]]*=' 2>/dev/null || true; } \
    | sed -E 's/^kind[[:space:]]*=[[:space:]]*"([^"]*)".*/\1/' 2>/dev/null \
    || true
}

_prompt_for() {
  local layout="$1" pos="$2"
  [ ! -f "$HOPEN_CONF" ] && return 0
  sed -n "/^\[layout\.${layout}\.panes\.${pos}\]\$/,/^\[/p" "$HOPEN_CONF" 2>/dev/null \
    | { grep -m1 '^prompt[[:space:]]*=' 2>/dev/null || true; } \
    | sed -E 's/^prompt[[:space:]]*=[[:space:]]*"(.*)"$/\1/' 2>/dev/null \
    || true
}

# 读 conf：layout.LAYOUT.panes.POS.pane_name
# 返回：echo 出 pane name 字符串。没配或配为空串都回退到 $pos（位置名本身），
# 作为默认 pane label。这样 hopen() 总是会填 pane_names，不会留空行。
_pane_name_for() {
  local layout="$1" pos="$2"
  local name=""
  if [ -f "$HOPEN_CONF" ]; then
    name=$(sed -n "/^\[layout\.${layout}\.panes\.${pos}\]\$/,/^\[/p" "$HOPEN_CONF" 2>/dev/null \
      | { grep -m1 '^pane_name[[:space:]]*=' 2>/dev/null || true; } \
      | sed -E 's/^pane_name[[:space:]]*=[[:space:]]*"([^"]*)".*/\1/' 2>/dev/null) || name=""
  fi
  if [ -z "$name" ]; then
    name="$pos"
  fi
  printf '%s' "$name"
}

# layout 代号 -> 该 layout 的 pane 总数（含 root）。
# _steps_for 输出 N 行 (N = split 次数)，总 pane 数 = N + 1。直接查表比运行
# _steps_for 更可靠，也避免 `$(( $(多行) + 1 ))` 被算术上下文当变量查的陷阱。
_panes_for() {
  case "$1" in
    12|21|111) echo 3 ;;
    13|31|22)  echo 4 ;;
    *)         echo 0 ;;
  esac
}

# Resolve a kind through _KIND_ALIASES. Unknown input passes through.
_resolve_kind() {
  case "$1" in
    op) echo "opencode" ;;
    cc) echo "claude"   ;;
    cd) echo "codex"    ;;
    pi) echo "pi"       ;;
    *)  echo "$1"       ;;
  esac
}

# 启动 agent。失败不阻断脚本，pane 留空；诊断信息仍写入 stderr。
# 参数：agent_name pane_id kind [prompt]
_start_agent() {
  local name="$1" pane="$2" raw_kind="$3" prompt="${4:-}" start_out kind
  kind=$(_resolve_kind "$raw_kind")

  # kind 本机不存在 → 跳过
  if ! command -v "$kind" >/dev/null 2>&1; then
    echo "[hopen] 跳过 $name: '$kind' 不在 PATH" >&2
    return 0
  fi

  if ! start_out=$(herdr agent start "$name" --kind "$kind" --pane "$pane" --timeout 60000 2>&1); then
    printf '%s\n' "$start_out" >&2
    echo "[hopen] 跳过 $name: '$kind' 启动失败（herdr agent start 返回非 0）" >&2
    return 0
  fi

  echo "[hopen] $name → $kind on $pane" >&2

  if [ -n "$prompt" ]; then
    herdr agent prompt "$name" "$prompt" >/dev/null 2>&1 || true
  fi
}

# hopen() — 按 conf 派位的入口函数。flag 在函数内解析，方便 source 后
# 外部代码（比如 hopen-once.sh）直接调用 same hopen() 而不受顶层 flag
# 解析的副作用影响。
#
# 输出契约：诊断日志写 stderr；stdout 只保留最后一行 `ws=... panes=...`。
hopen() {
  local no_agents=0
  local code=""

  # 内联 flag 解析
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-agents|-n) no_agents=1; shift ;;
      --help|-h)
        sed -n '2,18p' "${BASH_SOURCE[0]:-$0}"
        return 0
        ;;
      --) shift; break ;;
      -*)
        echo "hopen: 未知选项 '$1'。支持: --no-agents/-n / --help/-h" >&2
        return 2
        ;;
      *) code="$1"; shift; break ;;
    esac
  done

  [ -n "$code" ] || {
    echo "用法: hopen <12|13|21|31|22|111> [--no-agents|-n]" >&2
    return 2
  }

  # 按 created[] 顺序构造 pane names：读 conf 的 pane_name，未配则用位置名本身。
  local pane_names="" total
  total=$(_panes_for "$code")
  local i=0 pos name
  while [ $i -lt "$total" ]; do
    pos=$(_position_for "$code" "$i")
    name=$(_pane_name_for "$code" "$pos")
    pane_names+="${name}"$'\n'
    i=$((i+1))
  done

  _h_build_layout "$code" "" "" "" "$pane_names" || return 1

  local ws_id="${HOPEN_WS_ID}"
  local -a created=("${HOPEN_CREATED_PANES[@]}")

  # 跳到新 ws
  herdr workspace focus "$ws_id" >/dev/null 2>&1 || true

  # 按 conf 启动 agent（layout 段缺失 / pane 缺失 / kind 缺失 / kind 写错 全跳）
  if [ "$no_agents" -eq 0 ] && [ -f "$HOPEN_CONF" ]; then
    local i pos kind prompt
    for i in "${!created[@]}"; do
      pos=$(_position_for "$code" "$i")
      kind=$(_kind_for "$code" "$pos")
      prompt=$(_prompt_for "$code" "$pos")
      if [ -z "$kind" ]; then
        # pane 没在 conf 里配 → 跳过，pane 留干净 shell
        continue
      fi
      _start_agent "hopen-$code-$pos" "${created[$i]}" "$kind" "$prompt"
    done
  fi

  # 输出契约：诊断日志写 stderr；stdout 只保留这一条主结果，供调用方读取。
  # 调用方可从 ws=... panes=... 取得 workspace 和完整 pane 列表。
  echo "ws=$ws_id panes=${created[*]}"
  return 0
}

# 顶层入口：只有脚本被直接执行（不是被 source）时才跑 hopen()。
# 被 source 时 BASH_SOURCE[0] != $0，下面 if 不触发；外层 $@ 不被消费。
if [ "${BASH_SOURCE[0]:-}" = "$0" ]; then
  hopen "$@"
fi
