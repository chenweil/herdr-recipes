# herdr-recipes 发展建议

> 撰写日期：2026-09-22
> 初始参考版本：herdr-recipes 0.7.0；状态更新：2026-10-19，v0.8.0
> 横向参照：`eliasstravik/herdr-projects` 0.2.4（已读 `COORDINATOR.md` / `THREAD.md` / `adopt.rs` / `thread.rs` / `docs/herdr-notes.md`）、社区项目清单（见 `docs/herdr-research.md` §1）
> 状态：建议稿，未经评审；任何条目落地前应单独立项并复核当前 Herdr 版本契约

## 当前状态（2026-10-19）

下文保留最初建议的上下文；本表与各节状态说明用于区分已实现、候选和待决策项。

| 条目 | 当前状态 |
|---|---|
| §4.3 公共 conf 解析 | 已完成（`88df180`） |
| §4.4 Agent name 文档 | 已完成；`--explain` 为可选想法，未立项 |
| §4.6 已有能力测试 | 九布局派位、alias、Dispatch 和失败回滚已覆盖；去除继承重复收集 |
| §5.4 跨版本契约 | 0.8.2 / 0.9.5 两入口九布局隔离 smoke 通过；0.9.1 为历史记录 |
| §5.5 README 个人仓声明 | 已有 `Personal recipes` |
| §3.1 hstatus | 下一阶段单独立项；先做只读查询，不含 hook / watch |
| §4.1 / §4.2 / §4.5 | layout 抽表、alias 运行时读取、冲突检测尚未实现 |
| §3.2 / §3.3 / §3.5 | hook、sidebar、.hopenrc 均为候选，尚未评审 |
| §3.4 hclean | 与 ADR-0001 排除 cleanup 的边界冲突，需先决策，暂不实施 |
| §3.6 / §3.7 | PR followup 暂缓；Spec → Tickets 不做 |

本文不重述 ADR-0001 已确立的边界（Recipe Launcher ≠ Workflow Engine），也不重复 `herdr-research.md` 的事实记录。所有建议在此前提下提出。

---

## 1. 项目当前画像

| 层 | 现状 | 评价 |
|---|---|---|
| 布局覆盖 | 9 种代号（`11 12 21 22 13 31 111 221 122`）| 已饱和；继续新增代号边际收益低 |
| 派位入口 | `hopen.sh`（chord + `hopen-agents.conf`）+ `hopen-once.sh`（CLI 即开即用）| 双入口完整 |
| Agent 集成 | catalog v2、alias（`op / cc / cd`）、audit、dispatch notification | 工程化程度高 |
| 安装 / 升级 | managed block + `cp -p` 备份 + `herdr config check` + reload | 健壮，符合 §4 的边界 |
| 测试 | `tests/` 用 `tomllib` 锁住 keys / catalog / audit | 基础覆盖到位 |
| 文档 | README、scripts/README、CONTEXT、ADR、research | 结构清晰 |

**简短判断**：项目在"布局 + 派位 + 安装"这条主轴上已非常成熟。下一阶段的价值主要来自**横向能力的扩展**和**现有能力的可观测性提升**，而不是堆砌更多 layout 代号。

---

## 2. 横向参照：herdr-projects 可借鉴的部分

完整分析见 `docs/herdr-research.md` §1 社区项目。本节只提取与 herdr-recipes 边界契合的部分。

| herdr-projects 能力 | 与 Recipe Launcher 边界的关系 | herdr-recipes 是否应吸收 |
|---|---|---|
| Sidebar row（`$hp_state` / `$hp_activity` / `$hp`）| 仅改 herdr config，不触碰 workspace 生命周期 | **可吸收**（小工作量，install.sh 加 managed block 即可）|
| Progress hook（Claude Code / Codex）| agent 自报进度到 herdr，Recipe 边界内 | **可吸收**（给 pi / opencode 移植等价 hook）|
| Coordinator agent 中转调度 | 等价于 workflow engine | **不吸收**（违反 ADR-0001）|
| Worktree + 分支隔离 | 假设每个 thread 独立 PR | **不吸收**（与"同一分支多 agent 协作"习惯冲突）|
| PR followup routine | CI 失败自动回给对应 pane | **边界评估**（需要先确定"同一 PR / 多 pane"语义）|
| `machine list` + 跨机器 workspace | herdr 已有原生 `--machine` | **不封装**（CLI 够用，二次封装会变成 herdr 的影子）|

---

## 3. 发展建议（按优先级）

### 第一梯队：状态可观测性

herdr-projects 在 sidebar 显示 `needs you · ~55%` / `review · PR #4` / `working · 12m quiet` 这类行的体验非常直接，herdr-recipes 可以用更轻量的方式拿一部分价值。

#### 3.1 `hstatus.sh` — 当前 ws 的 pane 总览

**目标**：在一个 pane 里看到当前 ws 的全部 agent 状态摘要，无需切窗。

**契约**：

```bash
hstatus.sh                    # 当前 ws
hstatus.sh --workspace <id>   # 指定 ws
hstatus.sh --json             # JSON 输出，给未来的 popup / TUI 用
```

**输出形状**（人类可读）：

```
ws=w3 (main)  panes=4
  p1  codex  working   "implement feature A"   12m
  p2  pi     idle      "review the diff"       2m quiet
  p3  opencode blocked  "needs approval"        < 1m
  p4  claude  working   "fix failing CI"        5m
```

**实现要点**：

| 步骤 | 依赖 | 数据源 |
|------|------|--------|
| 读 pane 列表 | herdr 0.9.1+ | `herdr pane list --workspace <id>` |
| 读 agent 状态 | 已核对 0.9.5 CLI | `herdr agent list` 返回全量，再按 `pane_id` 与 pane 联接；没有 `--pane` 选项 |
| 读最近 activity | 尚未确认 | 只消费现有查询返回的已验证字段；`report-metadata` 是写入接口，不作为读取接口 |
| 持续刷新 | `watch` 或 ticker | `hstatus.sh --watch 5` |

**风险**：

- herdr 0.9.1 之前 snapshot 不包含 pane activity tokens（见 `herdr-projects/docs/herdr-notes.md` §4）；要先确认本机版本快照字段
- `hstatus.sh --watch` 不能阻塞现有 prefix 按键——只跑在 shell pane 里的子进程，按 `q` 退出

**初版范围**：只读的人类摘要与 JSON；缺失 activity / elapsed 字段输出未知，不推算。
`--watch`、新 hook 和 sidebar 配置后置。

**预估工作量**：小（半天）。不需要 install.sh 改动。

#### 3.2 Pi / OpenCode 的 progress hook

**目标**：让 herdr 自动知道 pi / opencode agent 的工作进度，side bar / popup 能展示。

**调研前置**（必须先做）：

| 问题 | 调研方法 |
|------|----------|
| pi 的 settings.json hook 系统与 Claude Code 等价吗？| 读 pi 文档 / 跑 `pi --help` / 看官方示例 |
| pi 是否接受 stdin hook 协议？| 实测最小 hook |
| OpenCode 是否有官方 hook API？| OpenCode 文档 + 实测 |
| herdr 端是否需要特定 metadata 协议？| `herdr pane report-metadata --help` / `docs/herdr-research.md` §2.1 |

**实现路径**：

1. 给 pi 写 `~/.pi/hooks.json`（或对应配置路径）的最小 stub：从 hook stdin 读事件、转成 `herdr pane report-metadata <pane> --source hp-activity --json <json>` 的 call
2. 同样给 OpenCode 写一份
3. `install.sh` 加 managed block 安装 hook 配置；`herdr-recipes unconfigure` 干净移除
4. `agent-audit.py` 加 hook 健康检查：每个 kind 是否检测到对应 hook 配置文件

**风险**：

- pi / OpenCode 的 hook 协议不一定与 Claude Code 等价；可能需要 adapter 层
- hook 配置位置在不同 agent 之间不统一（`~/.claude/settings.json` vs `~/.pi/hooks.json` vs `~/.opencode/`）；`install.sh` 要按 agent 类型分发
- hook 失败不能让 agent 启动失败——参考 herdr-projects 的策略：hook 是 best-effort，缺失就退回 herdr 自身检测

**预估工作量**：中（2-3 天，包含调研）。

#### 3.3 Sidebar row 配置

**目标**：让 herdr 的 side bar 自动显示 ws / pane 的 agent 状态摘要。

**最小版**：

```toml
# 仿 herdr-projects 的 configure 输出，简化版
[agents.row]
"$hp_state"   = "agent.status"
"$hp_activity" = "agent.last_activity"
```

**实现路径**：

- 在 `install.sh` 的 managed block 里写一份新的 sidebar 配置（独立于现有的 `[[keys.command]]` block，新加一个 marker）
- `unconfigure` 干净移除
- 与 herdr-projects 区分：**不接管** `prefix+a` 的全局键，留给用户自己配；herdr-recipes 只管 side bar 字段
- `config/keys.toml` 同步说明

**风险**：

- herdr config 改了 sidebar 后是否影响其他 plugin？需要 `herdr config check` 校验
- 与 herdr-projects 冲突：如果用户同时安装两个，managed block 不能互相覆盖——`install.sh` 要做存在性检测与告警

**预估工作量**：小（半天）+ 集成测试。

### 第二梯队：生命周期辅助

#### 3.4 `hclean.sh` — stale workspace 清理

**状态：待边界决策，不在当前执行队列。** ADR-0001 明确将 cleanup 排除在产品边界之外；
以下仅保留候选设计，实施前需明确是否及如何修订该决定。

**目标**：补齐 README 明说的"成功创建的 ws 不由脚本自动清理"边界。

**契约**：

```bash
hclean.sh                  # 列出候选 ws，等待确认后关闭
hclean.sh --dry-run         # 只列出，不动
hclean.sh --older-than 1d  # 只看超过 1 天没活动的
hclean.sh --json           # 给脚本调用
hclean.sh --yes            # 跳过确认
```

**判定 stale 的标准**（建议）：

| 标准 | 数据源 |
|------|--------|
| ws 内全部 pane idle 超过 N 分钟 | `herdr pane list` + agent status |
| ws label 是 `hopen-` 前缀且超过 1 天 | `herdr workspace list` + label 模式 |
| 用户显式排除的 ws（保留清单）| `~/.config/herdr/hopen-keep` 名单文件 |

**风险**：

- "stale" 的判定可能误杀正在跑的长任务；默认应该 `--dry-run`，要求 `--yes` 才真删
- 不能关掉"当前 focus 的 ws"——`herdr workspace focus` 自身会拒绝，但脚本要预检
- 跨 session 的 ws：`herdr workspace close` 在 native session 之外可能需要 `--session`

**预估工作量**：小（半天）。

#### 3.5 Layout 持久化（`.hopenrc`）

**目标**：让 repo 绑定默认 layout + 默认 agents，下次进同一个 repo 自动建议。

**最小契约**：

```toml
# .hopenrc 放在 repo 根
[branch.main]
layout = "22"
agents = ["codex", "codex", "pi", "claude"]

[branch.feature/x]
layout = "13"
agents = ["pi", "claude", "claude", "codex"]

[default]
layout = "12"
agents = ["codex", "pi", "claude"]
```

**接入方式**：

- `hopen.sh` / `hopen-once.sh` 加 `--suggest` flag：在当前 cwd 是 git repo 时读 `.hopenrc`，按当前分支返回建议 layout + agents（只打印，不真开）
- 用户审查后再决定用哪个
- 不修改 `hopen-agents.conf` 的语义；`.hopenrc` 是新的、用户主动放置的文件

**风险**：

- 容易过度设计：先做最小版（branch → layout + agents），工作流后置
- 与 `hopen-agents.conf` 的关系要明确：`.hopenrc` 是 repo 级，`hopen-agents.conf` 是用户全局级；同名时优先 repo 级

**预估工作量**：中（1-2 天）。

### 第三梯队：边界延伸（评估后决策）

#### 3.6 PR followup（边界评估）

**问题**：herdr-projects 的 `routines/pr-followup.md` 自动把 CI 失败 / review 意见回给对应 pane。herdr-recipes 要不要做？

**评估**：

| 维度 | herdr-projects 假设 | herdr-recipes 实际 |
|------|---------------------|---------------------|
| PR 与 pane 的对应 | 一个 thread 一个 PR | 多个 pane 共用一个 PR |
| 失败的回送目标 | 那个 thread 的 pane | 哪个 pane 写的那行？需要文件级溯源 |
| agent 跨 pane 协调 | 由 coordinator 中转 | 用户自己协调 |

**结论**：不建议近期做。当前多 pane 共用 PR 的语义下，"自动回送" 没有唯一目标 pane，会变成猜谜。如果要做，先回答一个前置问题：**用户怎么指定 PR 失败时哪个 pane 去修**——答案可能是显式声明，也可能是 git blame / commit author 反查。两者都不简单。

**建议**：把 PR followup 的需求先记到 issue tracker，等出现具体场景再立项。

#### 3.7 Spec → Tickets 工具

**判断**：不建议。`docs/herdr-research.md` §4 已明确不做 workflow engine；spec → tickets 是 workflow 的子集。如果用户需要，可以引用外部 herdr-workflows 或自己用 Markdown 维护。

---

## 4. 改进建议（针对现有代码）

### 4.1 `hopen.sh` 的 `_position_for` 与 `_steps_for` 重复映射

`hopen.sh` 同时维护：

- `_steps_for`：layout 代号 → split 步骤（parent_token + direction）
- `_position_for`：layout 代号 + 创建 idx → 视觉位置名
- `_panes_for`：layout 代号 → pane 总数

`hopen-once.sh` 又单独维护 `_h_row_major`：layout 代号 → (y, x) 视觉顺序。

三处映射都依赖 layout 代号，新增形状表时要改三处。**建议**：

- 抽出一个统一的 layout 元数据表（layout.toml 或 .hopen-layouts.json），三处都从这里查
- 新增 layout 代号时只改一处
- CHANGELOG 0.5.0 已记录"21/31 kind 派位改为 (y, x) 视觉顺序"的 bug 就是这种重复映射的代价

**风险**：

- 改动 `_position_for` 的语义会破坏现有 `hopen-agents.conf` 用户的位置名——必须保留向后兼容
- 抽表后建议先做"读表 + fallback 到现有硬编码"双轨过渡

**预估工作量**：中（1-2 天 + smoke 测试）。

### 4.2 `_resolve_kind` 的 alias 表硬编码

`_resolve_kind` 用 bash `case` 硬编码 `op → opencode` / `cc → claude` / `cd → codex`。**问题**：

- 新增 / 删除 alias 要改 shell 源码
- 没有用户自定义 alias 的入口
- 与 `config/agent-catalog.json` 的 aliases 重复

**建议**：

- 让 alias 表走 `config/agent-catalog.json`，与版本化的 catalog 统一
- `install.sh` 把 catalog 软链到 `~/.config/herdr/` 让用户能编辑自己的别名
- `_resolve_kind` 改为读 catalog 的 `aliases` 字段

**风险**：

- 启动性能（每次多读一次文件）——可加启动时缓存
- 用户改坏 catalog 导致 dispatch 失败——保留硬编码 fallback

**预估工作量**：小（半天）。

### 4.3 `_kind_for` / `_prompt_for` / `_pane_name_for` 的重复解析

**已完成**：`88df180` 抽出 `_read_section_key`，三个读取函数共用同一实现。
不再作为未来开发任务；固定格式子集的限制仍在 scripts/README 中说明。

### 4.4 `_agent_name` 的长度处理需要文档化

**文档已完成**：`scripts/README.md` 的“Agent name 格式”解释 32 字符上限、
截断和 6 位 hash。原建议中的 `--explain` 尚未实现，但没有实际使用需求，暂不立项。

### 4.5 install.sh 的 managed block 检测

当前 install.sh 用 `legacy_keys` 正则清理旧的 `prefix+(alt|ctrl)+1..9` 键。**问题**：

- 清理规则是手写正则，未来新增键要同步改
- 没有自动检测"这个 chord 是否已经由其他 plugin 管理"的能力

**建议**：

- 把清理规则改为"读现有 `~/.config/herdr/config.toml` 的 `[keys]` 段，遍历非 managed block 内的所有 `[[keys.command]]`，按本仓库管理的 chord 集合做交集删除"
- 增加 `install.sh --check-conflicts`：检测与已知插件的 chord 冲突，仅告警不修改

**风险**：

- 改动清理逻辑可能影响升级路径——需要保留旧行为做 dry-run 对照
- `--check-conflicts` 需要先有"已知插件列表"的数据源；先做最小版（仅 herdr-projects）

**预估工作量**：中（1 天）。

### 4.6 测试覆盖状态

| 能力 | 当前覆盖 |
|---|---|
| 九布局派位、pane name 与两个入口映射 | fixture 契约测试 |
| `_resolve_kind` alias / fallback | 与 catalog 的同步测试；运行时读取 catalog 仍未实现 |
| split 失败与回滚 | 两入口、每个 split 失败点、workspace close 兜底及残留诊断 |
| Dispatch failure 累积 | agent start / prompt 失败、继续尝试其它 pane、通知与 stdout 契约 |
| install 备份和卸载 | 隔离配置测试，包含备份内容和同秒重名保护 |
| 真实布局几何 | 0.8.2 / 0.9.5 各两入口九布局隔离 smoke |
| hook 健康检查 | 功能尚未立项，暂无测试义务 |

测试 fixture 与用例分离，61 项测试不再继承重复执行。后续功能在其实际调用入口
增加覆盖；更多 fuzz 或备份高频边界测试按实际问题追加。

---

## 5. 其他建议（流程 / 工程实践）

### 5.1 ADR 节奏

ADR-0001 写于 2026-09-03，至今未增。**建议**：

- §3.1 / §3.2 / §3.3 / §3.4 落地前都补 ADR（即使是"决定不做"的也要记）
- 模板参考 ADR-0001 的 frontmatter（status / date / context / decision / consequences）

### 5.2 issue tracker 用法

`AGENTS.md` 提到 issue tracker 在 GitHub Issues。**建议**：

- 历史 #1–#12 均已关闭；v0.8.0 收尾由 #13 跟踪。以 GitHub issue 状态为准，
  不再维护会重复漂移的手工编号清单；新功能在独立 issue 中记录范围和验收。
- §3 发展建议落地时按"先开 issue 再写代码"流程

### 5.3 CHANGELOG 与 VERSION 的同步

VERSION 单一来源、CHANGELOG 跟版本——这个流程很规范。**建议**：

- CHANGELOG 的"新增 / 变更 / 修复"分组保持现状
- "Unreleased" 段不要堆太多条目；超过 5 条就该切版本
- 落地 §3 时同步更新 CHANGELOG 与 README 引用

### 5.4 跨 Herdr 版本契约

`docs/herdr-research.md` 保留 0.8.2 / 0.9.1 / 0.9.5 的历史记录；v0.8.0 又用
官方 0.8.2 与本机 0.9.5 分别验证两个入口全部九布局。后续：

- 每次 Herdr 大版本升级后，跑一次 smoke matrix（`docs/herdr-research.md` §6 有现成模板），把结果 append 到 research.md
- catalog 的 min 表示契约下限（当前 0.8.2），recommended 表示实测推荐（0.9.5）；
  新 API 引入时补最低版本验证，不因推荐版本更新自动提高下限。

### 5.5 公开 API 边界

herdr-recipes 是个人配置仓库（CONTEXT、ADR-0001 都明确），但 README 写得像是公共工具。**两难**：

- 写得像公共工具：方便未来公开 / 复用
- 实际是个人配置：用户场景固定，硬要泛化会失去简洁

**建议**：

- README 已有 `Personal recipes` 声明，完成。
- 涉及"用户应该改哪些文件"的部分加 `> 你可以改` / `> 不要改` 标注
- 不要为了泛化而增加配置项——新增 flag 之前先问"是不是真有第二个用户"

### 5.6 文档组织

当前文档组织：

```
docs/
├── adr/                    # 架构决策
├── agents/                 # agent 工作指南（issue tracker / labels / domain）
├── herdr-research.md       # Herdr 调研记录
└── future-directions.md    # 本文
```

**建议**：

- 本文落地后，未来类似的发展建议统一放 `docs/roadmap/` 或 `docs/proposals/`，按编号归档（`0001-status-bar.md` / `0002-progress-hooks.md`）
- 不要让 future-directions.md 越长越大——每个建议落地后归档到 ADR，然后从 future 移除

---

## 6. 风险与边界提醒

| 风险 | 缓解 |
|------|------|
| §3.2 hook 移植受限于 pi / OpenCode 的 hook 系统能力 | 先做调研 spike，不直接立项 |
| §3.3 sidebar row 与 herdr-projects 冲突 | managed block 互相检测、文档化互斥说明 |
| §4.1 layout 元数据表改动破坏向后兼容 | 双轨过渡 + smoke matrix 对照 |
| §4.5 install.sh 清理逻辑改坏升级路径 | dry-run + 备份已存在，rollback 容易 |
| 任何发展建议都受 Herdr 版本契约约束 | 升级 Herdr 后重跑 smoke，research.md 同步 |

---

## 7. 当前落地顺序

1. 测试去重、失败回滚与最低版本验证（#13，v0.8.0）。
2. 同步事实、候选状态、版本与 Changelog；保留历史证据。
3. 发布 v0.8.0，记录通过的验证及未覆盖的环境。
4. 单独立项 §3.1 `hstatus`，先交付只读摘要与 JSON。
5. layout 抽表、alias 运行时读取和 installer 冲突检测按具体需求再立项。

hook / sidebar / `.hopenrc` 仍是候选；`hclean` 先解决 ADR 边界，PR followup 暂缓。

---

## 8. 与现有文档的关系

| 文档 | 关系 |
|------|------|
| `CONTEXT.md` | 词汇层，本文不重复定义 |
| `docs/adr/0001-keep-recipes-separate-from-workflows.md` | 边界层，所有发展建议遵守此 ADR |
| `docs/herdr-research.md` | 事实层，§1 社区项目清单是本文 §2 的输入 |
| `README.md` | 用户层，本文不重复 README 内容 |
| `scripts/README.md` | 脚本使用层，§4.4 的 agent name 格式应回写到这里 |

落地建议时应同步：

- 完成的项 → CHANGELOG + ADR（如有架构变化）
- 拒绝的项 → 在 issue tracker 标 wontfix + 链接回本文对应节
- 调研类（§3.2 / §3.6）→ research.md 新增章节

---

## 9. 自我边界声明

本文不是需求文档。任何 §3 / §4 的条目在落地前都需要：

1. 复核当前 Herdr 版本契约（可能与 0.9.1 不同）
2. 单独开 issue + ADR（如涉及架构）
3. smoke matrix 验证（参考 `docs/herdr-research.md` §6）

本文也不是对 herdr-projects 的官方评价；横向参照仅为产品决策提供线索，落地前需逐项复核。

---

## 10. 参考地址

> **关于本文对 `herdr-projects` 的引用**：本文 §2 / §3 / §6 / §9 多处直接讨论 herdr-projects 的能力与边界。为避免在正文中重复粘贴 URL，下文每次提到项目名 `herdr-projects` 时均不附链接，仓库地址统一见 §10.1。

### 10.1 横向参照的主项目

| 项目 | 地址 | 本文中对应章节 | 调研深度 |
|------|------|---------------|----------|
| herdr-projects | <https://github.com/eliasstravik/herdr-projects> | §2 全节、§3.2、§3.3、§3.6 | 已读 `COORDINATOR.md` / `THREAD.md` / `adopt.rs` / `thread.rs` / `docs/herdr-notes.md`，调研日 2026-09-22 |

### 10.2 生态项目（来自 `docs/herdr-research.md` §1）

| 项目 | 地址 | 用途 |
|------|------|------|
| awesome-herdr | <https://github.com/yigitkonur/awesome-herdr> | 生态导航 |
| herdr-spreader | <https://github.com/yuk1ty/herdr-spreader> | 声明式布局、validation、dry-run |
| herdr-workflows | <https://github.com/aorumbayev/herdr-workflows> | YAML workflow、等待、重试、运行历史（与 ADR-0001 边界相关）|
| herdr-catchup | <https://github.com/wilbeibi/herdr-catchup> | 跨 agent 会话总结、fork、handoff |
| herdr-pr-tracker | <https://github.com/Matovidlo/herdr-pr-tracker> | pane 与 PR / CI 操作关联（§3.6 PR followup 的替代候选）|
| herdr-worktreeinclude | <https://github.com/eightHundreds/herdr-worktreeinclude> | 为新 worktree 复制选定的 gitignored 文件 |

### 10.3 官方与文档

| 资源 | 地址 |
|------|------|
| Herdr 官方 | <https://herdr.dev> |
| Agent automation 文档 | <https://herdr.dev/docs/agent-automation/> |
| CLI reference | <https://herdr.dev/docs/cli-reference/> |
| Socket API | <https://herdr.dev/docs/socket-api/> |
| Session state and restore | <https://herdr.dev/docs/session-state/> |
| Integrations | <https://herdr.dev/docs/integrations/> |
| Configuration reference | <https://herdr.dev/docs/config-reference/> |

### 10.4 本仓库内部参考

| 路径 | 关系 |
|------|------|
| `CONTEXT.md` | 词汇层 |
| `docs/adr/0001-keep-recipes-separate-from-workflows.md` | 边界层 |
| `docs/herdr-research.md` | 事实层（Herdr 调研记录、社区项目清单、本机环境观察、smoke matrix）|
| `README.md` | 用户层 |
| `scripts/README.md` | 脚本使用层 |