# 提案 0001：受监督派发（Supervised Dispatch）

| 项 | 值 |
|---|---|
| 状态 | 已实现（0.10.0；实测记录见 `docs/herdr-research.md` §11，偏差已回写 §8/§9；退出码与 resume 语义已与实现对齐，见 §8.1 / §10） |
| 日期 | 2026-10-28 |
| 作者 | 对话式协调 + 用户决策 |
| 目标版本 | 0.10.0 |
| 前置决策 | ADR-0001 / ADR-0002 / ADR-0003 |
| 新增决策 | ADR-0004（本文 §12 收录已接受文本） |
| 关联 | `scripts/coordinator-prompt.md` §5、`scripts/hwait.py`、ADR-0003 |

---

## 1. 问题陈述

用户原话：

> 我最想解决一直盯着这些 agent 工作。目前我通过自然语言能跑起来流程，但是**不稳定**。比如某个 agent 报错后流程中断，**没有重试、重开的功能**。

> 先通过自然语言安排……也可以提供几个固定模式作为样板。如果他自然语言表达经常是一种，也可以提醒他写成固定流程。

现状缺口（已核对仓库代码）：

| 需求 | 现状 | 缺口 |
|---|---|---|
| 派工时指定 agent kind | `_start_agent` 支持 | ✅ |
| 派工时指定**模型** | `_start_agent` 调 `herdr agent start` **未传 `-- [AGENT_ARG]`** | ❌ 无法指定模型 |
| 多步骤 + 依赖触发 | 靠 `coordinator-prompt.md` 的 LLM 行为 | ⚠️ 不确定 |
| 固定流程样板 | 无 | ❌ |
| 事件驱动等待 | `hwait.py` 双信号 | ✅ |
| **失败后重试 / 重开** | 规则写在 §5，由 LLM 执行 | ❌ **不稳定（本提案核心）** |
| 形状重复时晋升为流程 | 无 | ❌ |

## 2. 核心判断

`coordinator-prompt.md` §5 已经定义了完整的恢复策略：

> 原会话最多恢复两次 → 重建一次同 kind 同角色 worker → 超限报告阻塞

**这套规则是对的，问题在于执行者是 LLM**。它要求执行者同时满足三个条件：恰好在等、记得住（上下文压缩前）、有空闲（自己不在处理别的分支）。任一不满足，流程就静默中断。

因此本提案不是「引入 workflow 引擎」，而是：

> **把 §5 已写好的恢复规则，从提示词搬进确定性脚本。同一套策略，换成不困不累不遗忘的执行者。**

这属于 ADR-0003 所说「协调者做有界恢复」的**机械化**，不是新增 Workflow 概念。

## 3. 边界（关键）

`hsuper` 含重试/重建，正面触碰 ADR-0001「Launcher 不拥有 retry/recovery」。按 ADR-0001 既有节奏，需新增 **ADR-0004** 收窄边界（文本见 §12）。

| ✅ 纳入 | ❌ 仍排除 |
|---|---|
| 会话作用域监督进程（run 结束或终端关闭即止） | 后台守护进程 / launchd 服务 |
| 有界重试（严格沿用 §5 的 2 次恢复 + 1 次重建） | 任务队列 / 调度器 |
| 同一 run 的状态落盘与 `--resume` | 会话结束后自动唤醒、自动恢复 |
| 平铺的 `next` 依赖链（有向、无环、非 DAG 表达） | YAML/DAG 工作流引擎、条件分支 DSL |
| 失败分支隔离，其它分支继续 | 自动 push / 发布 / 合并到远端 |

**一句话定位：受监督的派发（supervised dispatch），不是 workflow engine。**

## 4. 已拍板决策

| # | 决策项 | 选择 |
|---|---|---|
| 1 | `hsuper` 跑在哪 | **专用 supervisor pane**（可视、可打断） |
| 2 | flow 文件放哪 | **仓库内 `flows/`**（随项目走，可提交） |
| 3 | `gone` 重开位置 | **同 pane 同名**（复用布局） |
| 4 | 首批范围 | **`hsuper` + 一个 `dev-review` 样板 flow** |

## 5. 交付物

| 文件 | 动作 | 说明 |
|---|---|---|
| `scripts/hsuper.py` | 新增 | 监督器主体（Python 3，无第三方依赖） |
| `scripts/hsuper.sh` | 新增 | 薄壳，`exec python3 hsuper.py "$@"`，与 `hwait.sh` 同样式 |
| `flows/dev-review.toml` | 新增 | 样板流程：开发 → 审查 → 提交 |
| `flows/_schema.md` | 新增 | flow 文件字段规范（人读） |
| `docs/adr/0004-supervised-dispatch-boundary.md` | 新增 | §12 文本 |
| `scripts/README.md` | 追加 | `hsuper.sh` 与 `hwait.sh` 并列章节 |
| `README.md` | 追加 | 「固定流程」小节 + 文件表 |
| `CHANGELOG.md` | 追加 | Unreleased → 新增 |
| `VERSION` | 改 | `0.9.0` → `0.10.0`（随发布） |
| `tests/test_hsuper_flow.py` | 新增 | flow 解析 / 校验 / 计划生成单测 |
| `tests/test_hsuper_recovery.py` | 新增 | 恢复梯与 verdict 映射单测（注入假 verdict） |
| `tests/smoke_hsuper.py` | 新增 | 隔离 server 真实跑一通 `dev-review --dry-run` 与半程失败注入 |

## 6. 架构

```
                 用户一句话
                     │
        ┌────────────┴────────────┐
        │                         │
   探索性任务                 固定形状
        │                         │
   hteam.sh                  hsuper.sh --flow dev-review --task "..."
   (不变)                         │
                          ①创建 workspace + tab
                          ②创建 supervisor pane
                          ③在该 pane 内 re-exec hsuper.py（可见、可打断）
                                   │
                          ┌────────┴────────┐
                       flow 解析          state 落盘
                          │                 │
                  ┌───────┴────────┐   .herdr-recipes/runs/<id>/
                  │  监督循环       │     state.json
                  │  per step      │     prompts/<step>.md
                  │                │     result/<step>.json
                  │  hwait 双信号   │     logs/<step>.log
                  │  + 恢复梯       │     handoff/<step>-a<N>.md
                  └───────┬────────┘
                          │
              verdict → 动作（§9）
```

三层职责：

| 层 | 组件 | 职责 |
|---|---|---|
| L1 描述 | `flows/*.toml` | 声明角色、kind、model、提示词模板、`next` 链 |
| L2 执行 | `scripts/hsuper.py` | 建资源、派工、双信号等待、恢复梯、状态落盘 |
| L3 入口 | `hteam.sh`（不变）/ `hsuper.sh` | 自然语言探索 vs 固定流程 |
| L4 晋升 | `hflow suggest`（后续版本） | 读执行记录，重复形状 → flow 草案 |

## 7. Flow 文件规范

位置：仓库根 `flows/<name>.toml`。用户可用 `--flow-file PATH` 指定任意路径。

### 7.1 字段

| 键 | 层级 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|---|
| `description` | `[flow.<name>]` | string | 否 | `""` | 人读描述 |
| `max_parallel` | `[flow.<name>]` | int | 否 | `4` | 同时存活的 worker 上限；`0` = 不限 |
| `kind` | `[flow.<name>.defaults]` | string | 否 | `codex` | 默认 agent kind（支持 `op/cc/cd` alias） |
| `model` | `[flow.<name>.defaults]` | string | 否 | `""` | 空 = 沿用该 agent 自身配置 |
| `args` | `[flow.<name>.defaults]` | string[] | 否 | `[]` | 追加到 `herdr agent start ... --` 后的原生参数 |
| `role` | `[flow.<name>.steps.<id>]` | string | 否 | `<id>` | pane label / 报告里的角色名 |
| `kind` | 同上 | string | 否 | `defaults.kind` | 覆盖 |
| `model` | 同上 | string | 否 | `defaults.model` | 覆盖 |
| `args` | 同上 | string[] | 否 | `defaults.args` | 覆盖（非追加） |
| `prompt` | 同上 | string | **是** | — | 支持 `{{task}}` / `{{run_id}}` / `{{step}}` / `{{marker}}` / `{{run_dir}}` |
| `prompt_file` | 同上 | string | 否 | — | 与 `prompt` 二选一；相对仓库根的路径 |
| `next` | 同上 | string[] | 否 | `[]` | 完成后派发的步骤；空 = 叶子 |
| `expect` | 同上 | string[] | 否 | `[]` | 相对 cwd 的产物路径/glob；`gate` 用的证据 |
| `gate` | 同上 | `"artifact"` \| `"none"` | 否 | `"artifact"` when `expect` 非空 else `"none"` | 推进前是否必须看到产物 |
| `timeout_ms` | 同上 | int | 否 | `900000` | 单步单次等待预算 |
| `retries` | 同上 | int | 否 | `2` | 原会话恢复次数上限（**默认值锁定 §5 的 2**） |
| `rebuilds` | 同上 | int | 否 | `1` | 重建次数上限（**锁定 §5 的 1**） |
| `cwd` | 同上 | string | 否 | flow 的 `cwd` | 该步骤的 pane 工作目录 |

flow 级可选键：

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `cwd` | string | 用户 `-C` 或 `$PWD` | 团队工作目录 |
| `layout` | string | 自动 | 复用 hopen 布局代号；不写则按步骤数自动挑（≤2→`11`，3→`12`，4→`22`） |
| `start` | string[] | 所有入度为 0 的步骤 | 起始步骤 |

### 7.2 校验规则（`hsuper --dry-run` 必须全部报错）

1. 所有 `next` 指向的步骤必须存在
2. 依赖图必须无环（有环 → `flow: cycle detected: a -> b -> a`）
3. 每个步骤必须能从 `start` 到达（否则 `unreachable step`）
4. `prompt` 与 `prompt_file` 必须有且仅有一个
5. `kind` 必须落在 `herdr agent start --help` 的 possible values 内（运行时校验，不在解析期硬编码列表）
6. `retries` / `rebuilds` 不得超过 2 / 1（超过要求 `--allow-unsafe-limits`，默认拒绝 —— 防止把 2+1 悄悄改成无限重试）
7. 步骤 ID 匹配 `[a-z][a-z0-9_-]{0,31}`
8. `{{...}}` 占位符只允许 `task` / `run_id` / `step` / `marker` / `run_dir`；其它一律报错（防止手写近义名后静默留空）

### 7.3 样板：`flows/dev-review.toml`

```toml
# dev-review —— 开发 → 审查 → 提交。最小可用样板。
# 用法: hsuper.sh --flow dev-review --task "实现登录功能"

[flow.dev-review]
description = "开发 → 审查 → 提交"
# cwd 由 -C 决定（默认 $PWD），不在 flow 里写死
max_parallel = 2

[flow.dev-review.defaults]
kind = "codex"
model = ""                      # 空 = 沿用 codex 自身配置

[flow.dev-review.steps.dev]
role = "实现"
kind = "codex"
model = "gpt-5-codex"
prompt = """
任务：{{task}}

你是实现者。要求：
1. 先读 AGENTS.md / CONTEXT.md，遵守仓库既有约定
2. 只改本任务相关文件，不动无关代码
3. 完成后把结果写入 {{run_dir}}/result/dev.json：
   {"status":"done","changed_files":[...],"tests_run":N,"summary":"..."}
4. 在终端单独一行打印：{{marker}}
5. 失败时不要打印完成标记，把原因写进 result/dev.json 的 status="failed"
"""
expect = ["{{run_dir}}/result/dev.json"]
next = ["review"]

[flow.dev-review.steps.review]
role = "审查"
kind = "pi"
prompt = """
任务：审查 dev 步骤的实现。

输入：{{run_dir}}/result/dev.json 与工作目录的实际 diff。
只报阻塞问题，不要重写。完成后写入 {{run_dir}}/result/review.json，
在终端单独一行打印：{{marker}}。
"""
expect = ["{{run_dir}}/result/review.json"]
next = ["commit"]

[flow.dev-review.steps.commit]
role = "提交"
kind = "codex"
prompt = """
任务：提交 dev 步骤的改动。

要求：仅提交本任务涉及的路径，保护用户无关改动；
排除 .herdr-recipes/。不 push。
完成后写入 {{run_dir}}/result/commit.json，打印：{{marker}}。
"""
expect = ["{{run_dir}}/result/commit.json"]
```

## 8. `hsuper` CLI 契约

```
hsuper.sh --flow NAME --task TEXT [OPTIONS]
hsuper.sh --run RUN_ID [--resume]
```

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--flow NAME` | 二选一 | — | 从 `flows/NAME.toml` 加载 |
| `--flow-file PATH` | 二选一 | — | 任意路径加载 |
| `--run RUN_ID` | 否 | 自动生成 | 显式指定 run ID（配合 `--resume`） |
| `--resume` | 否 | — | 从 state.json 续跑未完成步骤 |
| `--task TEXT` | 首次必填 | — | 填入 `{{task}}`；`--resume` 时从 state 读 |
| `-C, --path PATH` | 否 | `$PWD` | 团队工作目录 |
| `--max-parallel N` | 否 | flow 的 `max_parallel` | 覆盖 |
| `--dry-run` | 否 | — | 只校验 + 打印计划，不创建任何资源 |
| `--foreground` | 否 | — | 不开 supervisor pane，直接在前台跑（调试用） |
| `--json` | 否 | — | 结束时输出 schema_version=1 JSON |
| `--allow-unsafe-limits` | 否 | — | 允许 `retries>2` / `rebuilds>1` |
| `--no-focus` | 否 | — | 不切换用户焦点 |
| `-v, -V, --version` | 否 | — | 复用 `version.sh` 的 `_hr_print_version` |
| `-h, --help` | 否 | — | 打印头部注释块 |

### 8.1 退出码

| 码 | 含义 |
|---|---|
| 0 | 全部步骤通过 |
| 1 | 启动 / 参数 / flow 校验错误 |
| 20 | 至少一个分支失败（恢复预算用尽） |
| 21 | 至少一个分支 `blocked`（等人工输入） |
| 22 | 全局超时预算耗尽，运行被截断（有步骤没派工） |
| 23 | 部分成功：有步骤被跳过，但没有失败/阻塞分支，且预算未耗尽 |

优先级 20 > 21 > 22 > 23 > 0：分支失败不会被包装成部分成功（ADR-0004）。
22 指预算把这次 run **截断**了（有步骤没派工）；预算到了但所有步骤都跑完，仍是 0。

这张表与 `flows/_schema.md`、`scripts/README.md` 是同一张表；改一处就要改三处。

### 8.2 启动流程（对应决策 1①）

`hsuper.py` 主进程（非 `--foreground`）：

1. 校验 flow、生成 `run_id`（4 hex）、创建 `.herdr-recipes/runs/<ts>-<run_id>/`
2. `herdr workspace create --cwd <cwd> --label <label> --no-focus` → 取 `workspace_id` / `root_pane` / `tab_id`
3. `herdr tab create --workspace <ws> --cwd <cwd> --label supervisor --no-focus` → 取 `tab_id` / `root_pane`
4. `herdr pane rename <supervisor_pane> supervisor`
5. `herdr pane run <supervisor_pane> "hsuper.py --run <run_id> --in-pane"` —— **re-exec 到 supervisor pane**
6. 主进程打印 `run=<id> ws=<ws> supervisor_pane=<pane> run_dir=<path>` 后退出 0

`--in-pane` 是内部参数：表示已在 supervisor pane 内，跳过步骤 2–5，直接进入监督循环，并把日志 tee 到 `logs/supervisor.log`。

> **为什么 re-exec**：用户选了「可视、可打断」。监督器自己的输出必须在 pane 里看得见，否则失败原因只能翻文件。

### 8.3 为什么 1① 不会变成后台守护

supervisor pane 里的进程生命周期 = pane 生命周期。pane 在 herdr session 内，session 结束它就没有了。这不是 daemon：没有 launchd、没有 self-restart、不跨 session 存活。终端关掉 = 流程停 = `--resume` 可续。

## 9. 监督循环

### 9.1 伪代码

```
for step in topological_order(flow):
    wait_until_all(deps of step are DONE)

    agent_pane = ensure_pane(step)        # 复用或新建
    marker     = f"DONE {run_id}-{step}"  # 13 字符，避免换行拆断
    write_prompt_file(step, marker)       # 关键：提示词走文件，见 §9.3
    start_agent(step)                     # herdr agent start ... -- <args>
    submit(step)                          # herdr agent prompt <name> "读取 <file> 并执行"

    attempts = resume = rebuild = 0
    while True:
        v = hwait(agent=name, pane=pane, marker=marker, timeout=step.timeout_ms)

        if v == marker:
            if gate_ok(step): break                       # 推进
            else: rearm(); continue                        # 回显误报，见 §9.3
        elif v == settled:
            if result_file_says_done(step): break
            action = classify_error(step)                  # 读 result/<step>.json + 日志
            apply_recovery(action)
        elif v == blocked:
            notify_human(step); mark(step, BLOCKED); goto next_branch   # 不阻塞其它分支
        elif v == gone:
            apply_recovery("restart")                      # ← 本提案核心缺口
        elif v == timeout:
            if progressing(step): continue                 # 仍在跑，再等一轮
            apply_recovery("resume")
        elif v == unknown:
            if output_shows_progress(step): continue
            apply_recovery("resume")
```

### 9.2 verdict → 动作

| verdict | 退出码 | 动作 |
|---|---|---|
| `marker` | 0 | 校验 gate：`expect` 全部存在且 `result/<step>.json` 可解析 → 推进 `next` |
| `settled` | 10 | 读 `result/<step>.json`：`status=done` → 推进；`failed`/缺失 → 恢复梯 |
| `blocked` | 11 | `notification show` 提醒人；该分支记 `BLOCKED`，**其它分支继续** |
| `gone` | 12 | **重开**：同 pane 同名重启 + 交接提示（见 §9.4） |
| `timeout` | 13 | 读 `pane process-info` 判定：有前台进程 → 再等一轮；无 → 恢复梯 |
| `unknown` | 14 | 读 `--source visible` 输出：有推进迹象 → 继续等；否则恢复梯 |

### 9.3 完成标记的回显陷阱（必须按此实现）

`NOTES.md` 已记录的坑：`pane wait-output` **会立刻搜索现有快照**，所以标记串只要出现在屏幕上（包括你敲进去的命令行）就会误报。

本设计的对策（双保险）：

1. **提示词走文件**：`prompts/<step>.md` 落盘，终端只出现
   `读取 /path/prompts/dev.md 并执行` —— `DONE r7f3-dev` 从未出现在屏幕。
2. **marker 只是候选**：即便命中，仍必须通过 `gate_ok(step)`（产物存在 + JSON 可解析）才推进；
   命中但 gate 不过 → 判定为回显误报，重新武装等待并写一条 `logs/<step>.log` 说明。

同样适用于 `result/<step>.json`：worker 必须**写文件**而不是只打印（仓库既有规则 #7：agent 间传数据走文件）。

### 9.4 恢复梯（严格沿用 §5 的数字）

| 序 | 触发 | 动作 | 记录 |
|---|---|---|---|
| 1–2 | `settled` 报错 / `timeout` 停滞 / `unknown` | **原会话恢复**：`agent prompt <name> "继续任务 <run>-<step>。已收到你的报错：<摘要>。检查已有改动，从中断点完成剩余工作，仍按 prompts/<step>.md 的约定交付。"` | `handoff/<step>-a<N>.md` |
| 3 | 原会话恢复失败（连续无新进展或 `gone`） | **重建**：先确认旧进程已停（`pane process-info` 无 agent 前台进程）→ 同 pane 同名 `agent start` → 传交接记录（基线 commit、已有 diff、错误摘要、剩余验收） | `handoff/<step>-rebuild.md` |
| 4+ | 重建后仍同类故障 | **分支失败**：`state.json` 记 `status=failed` + 原因 → `notification show` → **其它分支继续** | `state.json` |

`gone` 直接进重建（原会话已不存在）。重建不占额外 worker 名额，也不允许 worker 自行扩充团队。

**同 pane 重建的安全前置检查**（决策 3①）：

```
pane process-info --pane <pane>
  → foreground_processes 里除 shell 外无进程  ⇒ 可直接 agent start
  → 仍有 agent 进程（未完全退出）            ⇒ 先 agent send-keys <name> ctrl+c 或等待
  → pane 已随终端消失                        ⇒ 回退：pane split 同 tab 新建 pane，记 deviation
```

回退必须写进 `state.json` 的 `deviations[]`，并在最终报告里说明 —— 不允许静默偏离决策 3①。

## 10. 状态文件

路径：`.herdr-recipes/runs/<YYYYMMDD-HHMM>-<run_id>/state.json`（已在 `.gitignore` 覆盖范围，需确认）。

```json
{
  "schema_version": 1,
  "run_id": "r7f3",
  "flow": "dev-review",
  "task": "实现登录功能",
  "cwd": "/Users/chenweilong/project/x",
  "started_at": "2026-10-28T14:02:11+08:00",
  "updated_at": "2026-10-28T14:19:44+08:00",
  "workspace_id": "w9A",
  "tab_id": "w9A:t2",
  "supervisor_pane": "w9A:p1",
  "steps": {
    "dev": {
      "status": "done",
      "role": "实现",
      "kind": "codex",
      "model": "gpt-5-codex",
      "agent_name": "hsuper-r7f3-dev",
      "pane_id": "w9A:p2",
      "session_path": "/Users/.../2026-10-28T06-05-11.jsonl",
      "attempts": { "resume": 1, "rebuild": 1 },
      "result_file": "/Users/.../result/dev.json",
      "started_at": "...",
      "finished_at": "..."
    },
    "review": { "status": "blocked", "reason": "等待授权执行测试" }
  },
  "deviations": [
    { "step": "dev", "kind": "restart_new_pane", "why": "old pane not at shell prompt" }
  ]
}
```

`status` 取值：`pending` / `running` / `done` / `failed` / `blocked` / `skipped`。

**resume 语义**：`--resume` 重跑 `status in {pending, running, failed, skipped}` 的步骤（`skipped` 的步骤从没跑过，它的工作还欠着这次 run）；选中集合对依赖封闭——一个步骤只有在它的依赖已 `done` 或本轮也会重跑时才被选中；`done` 步骤跳过且不重新派工；`blocked` 步骤保持（除非 `--retry-blocked`，v0.11 再议），并且这次 run 仍然以 **21** 结束——还有分支在等人，run 就没算完成。

## 11. 报告

### 11.1 人读（结束时打印到 supervisor pane）

```
run r7f3  flow dev-review  用时 17m42s
─────────────────────────────────────────
dev       ✅ done     2m10s   gpt-5-codex   恢复1 重建1
review    ⚠️ blocked  —       pi            等人工授权
commit    ⏸ skipped  —       —             前序未完成
─────────────────────────────────────────
交付: .herdr-recipes/runs/20261028-1402-r7f3/
下一步: 处理 review 的阻塞后 hsuper.sh --run r7f3 --resume
```

### 11.2 JSON（`--json`）

```json
{"schema_version":1,"exit_code":20,"run_id":"r7f3","flow":"dev-review",
 "elapsed_s":1062,"run_dir":"...","steps":[{"id":"dev","status":"done",...}],
 "deviations":[...]}
```

## 12. ADR-0004（已接受文本）

> 文件：`docs/adr/0004-supervised-dispatch-boundary.md`

```markdown
---
status: accepted
date: 2026-10-28
---

# Supervised Dispatch: Bounded Retry/Rebuild Inside the Launcher

## Context

ADR-0001 keeps retries and recovery outside the launcher boundary. In practice the
conversational coordinator (ADR-0003) already performs bounded recovery, but as model
behavior: it must be waiting, remember the task, and be otherwise free at the moment a
worker dies. Users report flows that break silently on worker error with no retry or
restart.

## Decision

Add one deterministic supervisor (`hsuper`) that executes the recovery policy already
documented in `coordinator-prompt.md` §5, for a single declarative flow:

- Scope is one run, inside one Herdr session, launched into a dedicated supervisor pane.
- Retry limits are the existing §5 numbers: 2 original-session resumes, then 1 rebuild,
  then the branch is marked failed and other branches continue.
- Flow definitions are a flat directed `next` chain, not a DAG expression language.
- State is written per run so `--resume` can continue the same run after a restart.

It does not add a background daemon, a task queue, cross-session auto-resume, YAML
conditionals, or automatic publishing.

## Consequences

- ADR-0001's prohibition narrows from "no retries at all" to "no retry runtime outside a
  live supervised run". Documentation stating the launcher never retries must be updated.
- The 2+1 ladder becomes executable policy; changing it requires an ADR amendment, not a
  prompt edit. Over-limit values are rejected unless `--allow-unsafe-limits`.
- Recovery is bounded and attributable: every attempt lands in `state.json` and
  `handoff/`. A dead supervisor pane is not auto-restarted.
- The conversational coordinator remains the entry point for exploratory work; `hsuper`
  only serves shapes the user has committed to a flow file.
```

## 13. 非目标

- 后台守护、launchd、跨会话自动恢复
- 任务队列、优先级调度、cron
- YAML/条件分支/DAG DSL
- 自动 push / 发布 / 开 PR
- 自动判定「代码质量合格」（review 仍是 worker 的活，协调者只核验证据）
- 替换 `hteam`：探索性任务继续走自然语言协调
- `hflow suggest`（形状识别与晋升建议）—— v0.11 单独提案

## 14. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 标记回显导致假完成 | 提前推进，下游跑偏 | 提示词走文件 + marker 仅作候选 + 强制 gate 校验（§9.3） |
| 同 pane 重建失败（agent 未完全退出） | 重建卡住 | `pane process-info` 前置检查 → 新 pane 回退 + `deviations[]`（§9.4） |
| `agent start` 要求 pane 在 shell 提示符 | 重启失败 | 重建前检查前台进程；失败计入重建次数，不无限重试 |
| 2+1 上限对长任务不够 | 分支失败 | 上限是**策略**不是**能力**：`--allow-unsafe-limits` 显式放宽 + 记入 state |
| supervisor pane 被用户关闭 | 流程静默中断 | pane 关闭后 `agent_not_found`；`--resume` 从 state 续跑 |
| 不同 agent 的 `--model` 参数不一致 | 指定模型失败 | `model` 展开为 `--model <m>`；需要差异时用 `args = [...]` 原样透传 |
| flow 写错导致误派工 | 浪费资源 | `--dry-run` 必须能独立校验全部规则；默认 `gate=artifact` |
| 与 `hteam` 职责重叠 | 用户困惑 | 文档明示：探索 → `hteam`；形状固定 → `hsuper` |

## 15. 实施任务拆解（给开发 agent）

> 按序执行；每步有可验证的验收条件。**不要跳过 §16 的实测要求。**

### T1 · flow 解析与校验

- 新增 `scripts/hsuper.py`，实现 `load_flow()` / `validate_flow()` / `build_plan()`
- 用 `tomllib`（Python 3.11+，与现有测试一致）；Python 3.10 及以下报错提示
- **验收**：`python3 -m unittest tests.test_hsuper_flow -v` 全绿；覆盖 §7.2 全部 8 条规则
- **验收**：`hsuper.sh --flow dev-review --task x --dry-run` 打印计划且**不创建任何资源**（用 `herdr workspace list` 计数前后差为 0 证明）

### T2 · 模板渲染与提示词落盘

- `{{task}}` / `{{run_id}}` / `{{step}}` / `{{marker}}` 渲染；未知占位符报错
- 写 `prompts/<step>.md`；提交给 agent 的内容**不含** marker 字面量
- **验收**：单测断言「提交给 `agent prompt` 的字符串里不出现 `DONE <run_id>-<step>`」，而 `prompts/<step>.md` 里出现

### T3 · 资源编排（复用 hopen.sh 原语）

- `source scripts/hopen.sh` 复用 **低层** 原语：`_h_ws`、`_h_split`、`_steps_for`、`_position_for`、`_resolve_kind`、`_agent_name`
- **不要用 `_h_build_layout`**：它的签名是 `(code, ws_label, cwd, tab_label, pane_names)`，会一次性创建 workspace 并在**同一个 tab** 内建全部 pane；`hsuper` 需要「ws + supervisor tab + worker tab」的三段结构，`_h_build_layout` 的语义不匹配
- 步骤到 pane 的映射顺序必须走 `_position_for`（视觉顺序），不能按 `created[]` 顺序 —— 这是 CHANGELOG 里 21/22/31 派位 bug 的根因
- `_start_agent` 需增加可选第 5 参数 `agent_args`（数组），拼到 `herdr agent start ... -- <args...>`；**现有 4 参数调用的行为必须完全不变**
- **验收**：隔离 server smoke 里 `pane layout` 返回的 pane 数与 flow 步骤数一致
- **验收**：`tests/test_hopen_dispatch.py` 现有用例全绿（证明 `_start_agent` 未被破坏）

### T4 · 监督循环 + 恢复梯（本提案核心）

- `wait_step()`：**import 复用** `scripts/hwait.py`（它有 `if __name__ == "__main__"` 保护，可直接 import）。两种接法：
  - 高层：构造 `argparse.Namespace(agent=…, pane=…, marker=…, timeout=…, slice=60000, marker_timeout=1500)` 调 `wait_for(args)`
  - 低层：直接调 `marker_check(pane_id, marker, timeout_ms)` 与 `wait_state(target, slice_ms)` 自行组合分片
  - **不要 shell out 到 `hwait.sh`**
- `gate_ok()`：`expect` 存在性 + `result/<step>.json` 可解析
- `apply_recovery()`：resume ×2 → rebuild ×1 → 分支失败
- 分支隔离：一个步骤 `failed`/`blocked` 不影响无依赖关系的其它步骤
- **测试手法沿用仓库既有惯例**：参考 `tests/test_hwait.py` —— 在 `PATH` 前置一个假的 `herdr` 可执行文件，用环境变量喂**按序出队的 verdict**（`HWAIT_FAKE_STATE` 那套），不要往生产代码里加测试开关
- **验收**：`tests/test_hsuper_recovery.py` 用假 verdict 序列验证：
  - `[gone, marker]` → 恰好 1 次 rebuild，最终 done
  - `[settled(failed), settled(failed), settled(failed), marker]` → 2 次 resume 后 rebuild
  - `[settled(failed) × 4]` → 分支 failed，退出码 20，其它分支仍完成
  - `[blocked]` → 分支 blocked（退出码 21），其它分支继续

### T5 · 状态落盘与 resume

- 每次状态变更原子写（写 tmp → `os.replace`）
- `--resume` 语义按 §10
- **验收**：smoke 中被 kill 的 run，`--resume` 不重复派工已 `done` 的步骤

### T6 · 样板 flow + 文档

- `flows/dev-review.toml`、`flows/_schema.md`
- `scripts/README.md` / `README.md` / `CHANGELOG.md` 追加
- `docs/adr/0004-...md` 落地（§12 文本，`status: accepted`）
- **验收**：`bash -n` 全通过；README 里每条命令都实测过（仓库硬规矩）

### T7 · 发布

- `VERSION` → `0.10.0`；`CHANGELOG` 切段
- **验收**：`python3 -m unittest discover -s tests -v` 全绿

## 16. 实测要求（不许凭 --help 推断的部分）

### 16.0 设计期已核实的前提（无需重测，开发 agent 可直接依赖）

| # | 结论 | 证据 |
|---|---|---|
| 1 | `.herdr-recipes/` **已被 `.gitignore` 覆盖** | `git check-ignore -v` → `.gitignore:13:.herdr-recipes/` |
| 2 | `tomllib` 可用（解释器 3.14.6） | `python3 -c "import tomllib"` OK |
| 3 | `hwait.py` 可 import（`if __name__ == "__main__"` 保护），对外入口 `wait_for(args)` / `marker_check()` / `wait_state()` | 读源码 |
| 4 | `_start_agent` 当前**未传** `-- [AGENT_ARG]`，故无法指定模型 | `scripts/hopen.sh` 源码 |
| 5 | `herdr agent start` 支持 `-- [AGENT_ARG]...` | `herdr agent start --help` |
| 6 | `herdr pane process-info` 返回 `foreground_processes[].argv0` 与 `shell_pid`，可用于判定 pane 是否空 | 真实调用 `w8D:p1`（返回 `node`/`pi` + `shell_pid`） |
| 7 | `herdr agent list` 的 `name` 字段**仅命名 agent 才有**；未命名 agent 只能用 pane ID 定位 | 真实 `agent list` 输出 |
| 8 | `herdr notification` 只有 `show` 子命令（无 `send`），参数 `--body/--position/--sound` | `herdr notification` 输出 |

### 16.1 仍需实测的部分

| # | 待测 | 方法 |
|---|---|---|
| 1 | agent 退出后，pane 是否自动回到 shell 提示符 | 起一个 agent → `agent send-keys <n> ctrl+c` 或让它退出 → `pane process-info` 看 `foreground_processes` |
| 2 | 同 pane 同名重建是否可行、耗时多久 | 退出后 `agent start <同名> --pane <同 pane> --timeout 60000`，记录返回 |
| 3 | `agent start -- --model X` 在各 kind 的实际效果 | 对 codex / pi / claude 各试一次，记录失败诊断 |
| 4 | `hwait` 对“agent 已被 SIGKILL”的 `gone` 延迟 | 已有记录（6s / 0.009s），复跑确认 0.9.5 不变 |
| 5 | `pane run` 在 supervisor pane 内 re-exec 的行为 | 确认 `--in-pane` 不递归、日志能 tee |

任何一项与预期不符 → 修正设计后再继续，**不得先写代码后补验证**。

## 17. 文档同步清单

- [x] `docs/proposals/0001-supervised-dispatch.md`（本文）
- [x] `docs/adr/0004-supervised-dispatch-boundary.md`
- [x] `flows/dev-review.toml` + `flows/_schema.md`
- [x] `scripts/README.md`：`hsuper.sh` 章节（与 `hwait.sh` 同层级）
- [x] `README.md`：文件表 + 「固定流程」小节 + 与 `hteam` 的选择说明
- [x] `CHANGELOG.md`：Unreleased 新增
- [x] `CONTEXT.md`：如引入新词（**Supervised Run**），补词条；否则不动
- [x] `docs/future-directions.md`：§3 增加「受监督派发」状态条目
- [x] `docs/herdr-research.md`：§11 六项实测结果
