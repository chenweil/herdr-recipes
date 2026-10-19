# Herdr 调研与验证记录

> 调研日期：2026-09-03
> 验证环境：Herdr 0.8.2 + Herdr 0.9.1（五 pane 布局）+ Herdr 0.9.5（全 9 布局
> smoke，2026-10-19），macOS
> 方法：官方文档、本机 CLI/schema、仓库源码和社区项目交叉核验
> 状态：事实已复核；产品边界已经讨论确认；issue #2–#8 已完成实现与 smoke 记录，
> #10（0.9.5 复跑九布局）已核销

## 1. 资料与证据等级

### 主要依据

| 资料 | 用途 |
|---|---|
| [Agent automation](https://herdr.dev/docs/agent-automation/) | workspace、pane、agent 三类控制面及等待语义 |
| [CLI reference](https://herdr.dev/docs/cli-reference/) | 当前 CLI 命令与参数 |
| [Socket API](https://herdr.dev/docs/socket-api/) | socket 方法与响应结构 |
| [Session state and restore](https://herdr.dev/docs/session-state/) | detach、restart、native agent resume 的边界 |
| [Integrations](https://herdr.dev/docs/integrations/) | integration 的用途、安装目标和副作用 |
| [Configuration reference](https://herdr.dev/docs/config-reference/) | 键位、sidebar、notification、session 配置 |
| `herdr --skill` | 与本机版本匹配的 agent 操作约束 |
| `herdr --default-config` | 与本机版本匹配的配置项 |
| `herdr api schema --json` | 与本机版本匹配的 socket schema |

Herdr 源码中的 `docs/versions/<version>/` 可用于版本差异核对。`compare` 页面和
DeepWiki 只作为辅助材料，不作为 CLI 或行为契约的权威来源。

### 已核验的社区项目

| 项目 | 已确认的覆盖范围 |
|---|---|
| [awesome-herdr](https://github.com/yigitkonur/awesome-herdr) | 生态导航 |
| [herdr-spreader](https://github.com/yuk1ty/herdr-spreader) | 声明式布局、validation、dry-run |
| [herdr-workflows](https://github.com/aorumbayev/herdr-workflows) | YAML workflow、agent/run/herdr step、等待、重试、运行历史 |
| [herdr-catchup](https://github.com/wilbeibi/herdr-catchup) | 跨 agent 会话总结、fork 和 handoff |
| [herdr-pr-tracker](https://github.com/Matovidlo/herdr-pr-tracker) | pane 与 PR/CI 操作关联 |
| [herdr-worktreeinclude](https://github.com/eightHundreds/herdr-worktreeinclude) | 为新 worktree 复制选定的 gitignored 文件 |

其他生态项目仍可作为线索，但在形成产品决策前需要逐项复核其当前版本与真实能力。

## 2. Herdr 0.8.2 已验证事实

本节将 Herdr CLI/schema 的可复核事实与本机环境观察分开记录。只有前者可作为
Herdr 行为契约；后者只描述本次验证环境，不能推断所有安装环境都相同。

### 2.1 CLI 与生命周期（CLI/schema 实测）

- `herdr session` 只有 `list | attach | stop | delete`，没有 `current`。
- `workspace create` 同时创建 root tab 和 root pane，并在 JSON 中返回三个对象。
- `pane layout --current` 返回 workspace、focused pane、pane rectangles 和 split tree。
- `agent start --kind` 在现有 shell pane 中启动 Herdr 支持的 agent，并等待其可交互。
- agent 生命周期是 `idle | working | blocked | done | unknown`。
- `done` 是未查看的 settled/idle 注意力状态，不是进程退出码或任务成功证明。
- `blocked` 只表示 Herdr 识别到了需要输入或授权的 UI，不代表所有逻辑卡住情况。
- `agent prompt --wait` 等待 lifecycle settled，不跟踪某个 prompt 对应的独立 turn。
- `pane wait-output` 匹配原始终端输出，并会立即命中已有屏幕文本；它不是 agent
  lifecycle 的替代品。
- agent name 必须匹配 `[a-z][a-z0-9_-]{0,31}`，并在 live agents 中唯一。
- pane 移动到其他 workspace 后 ID 会改变；应继续使用响应返回的新 ID 或 agent name。

### 2.2 支持的 agent、executable 与 integration 是不同集合（CLI 实测）

`herdr agent start --help` 在 0.8.2 中列出 22 个 canonical kinds：

```text
pi claude codex gemini cursor devin agy cline omp mastracode opencode
copilot kimi kiro droid amp grok hermes kilo qodercli qwen maki
```

这些 kind 不等于 integration targets，也不能假定总与 PATH 中的 executable 同名：

- 本机检测 manifest 将 kind `cursor` 与 `cursor-agent` 关联，将 kind `agy` 与
  `antigravity` / `antigravity-cli` 关联；这是基于 manifest alias 和 PATH 的环境推断，
  不是 Herdr 公布的 executable 映射契约。
- CLI 文档和本机安装状态使用的 integration target 拼写可能不同：命令行目标是
  `antigravity-cli`，socket schema 的枚举值是 `antigravity_cli`。
- 部分 kind 没有官方 integration，但仍可由 `agent start` 启动和由屏幕规则检测。

因此，`command -v "$kind"` 不是可靠的启动门禁。运行时应直接调用
`herdr agent start --kind`，由 Herdr 负责 kind 校验、canonical executable 选择、
PATH 查找和 readiness 判断。

Herdr 0.9.1 重测（2026-09-19）：
- `herdr agent start --help` 列出 **24 个** canonical kinds，比 0.8.2 的 22 个
  多出 `letta` 与 `muse`；本仓库已 bump `config/agent-catalog.json` 到 catalog v2。
- `herdr integration install --help` 的 possible values 含 **18 个** integration
  target（pi, omp, claude, codex, copilot, devin, droid, kimi, opencode, kilo,
  hermes, qodercli, qwen, cursor, mastracode, antigravity-cli, grok, letta），
  其中 `letta` 在状态输出里以 `letta (experimental): ...` 形式出现，解析时
  需剥离 ` (...)` 后缀才能匹配 catalog。`muse` 不在 integration target 列表中，
  故 `muse.integration_targets` 留空。

### 2.3 Herdr 没有公开“已安装 agent”查询（CLI/schema 事实）

Herdr 设置界面会为 PATH 中找到的 agents 推荐 integrations，但 0.8.2 没有公开 CLI
或 socket API 返回这份 discovery 结果。容易混淆的接口分别回答不同问题：

| 接口 | 实际含义 |
|---|---|
| `herdr agent start --help` | Herdr 二进制支持的 start kinds |
| `herdr integration status` | integration hook/plugin 是否安装或过期 |
| `herdr server agent-manifests --json` | 当前 agent 屏幕检测规则及其版本和来源 |
| `herdr agent list` | 当前 session 中 live/detected agents |

所以 integration status 不能代替 executable discovery，agent manifests 也不能证明
executable 存在。

### 2.4 本机环境观察（2026-09-03，Herdr 0.8.2）

- 本机的 `kimi` 和 `cursor-agent` executable 已安装，但对应 integration 未安装。
- `server agent-manifests --json` 返回 20 个 manifest；`omp` 和 `mastracode` 出现在
  `agent start --help` 的 22 个 kind 中，却不在本机 manifest 集合中。
- manifest 状态包含 remote/bundled 来源、缓存版本和最近检查结果；因此 manifest 集合
  可能随远程更新变化，不能当作固定的 supported-kind catalog。

### 2.4a 本机环境观察（2026-09-19，Herdr 0.9.1）

- 本机 Herdr 升级到 0.9.1；`python3 --version` 报 Python 3.14.6。
- 24 个 kind 中：`letta` 与 `muse` 的 executable 均缺失（PATH 中没有 `letta` /
  `muse` 可执行文件），其他 kind 的 executable 与 2026-09-03 观察基本一致。
- 0.9.1 的 integration status 输出在 target 后追加 ` (...)` 后缀（如
  `letta (experimental): not installed (...)`），catalog 解析路径已加 ` (...)`
  剥离避免误报为 unknown。

### Integration 与恢复边界

- Herdr 可在 server restart 后恢复 workspace/tab/pane 的形状和 cwd。
- 0.8.x 的 native conversation resume 使用 official integration 提供的 session
  reference；0.9.2 起也允许 agent 自报 resume 命令，并非只能依赖内置集成
  （见上游追踪 §二-1）。这不代表任意 agent 已在本机验证恢复成功。
- 这不等于恢复 workflow 的 DAG、retry、result 或 cleanup 状态；这些属于 workflow
  runner 自己的持久化协议。
- `integration install` 会写入 Claude、Codex、Pi 等工具的用户配置，因此
  herdr-recipes 只做检查和提示，不自动安装。

## 3. herdr-recipes 当前状态

当前版本为 0.8.0。仓库提供固定 pane layouts、可选 agent 派位、初始 prompt、pane
数字切换、键位配置和 installer。

### 已经成立的实现

- workspace、tab、root pane 和 split pane ID 都从 Herdr JSON 响应读取，没有猜测 ID。
- `agent start --timeout 60000` 等待 agent 可交互，没有依赖 sleep。
- workspace create 和所有 split 已使用 `--no-focus`；完成后再显式切换 workspace。
- split 失败会回滚新建 workspace；单个 agent 失败不会破坏成功的布局。
- installer 用 marker 管理自己的 keybinding block，并备份被替换的 scripts 目录。

### Issue #2 已实施

1. 旧的 Shell 切换器使用多项旧 CLI/JSON contract：不存在的 `session current`、无效的
   `--json`、旧字段名和旧的 pane focus 调用。当前键位实际使用 Python 版本，Shell
   版本自身也依赖 Python，继续维护没有价值；本 issue 已将其移除。
2. `herdr-pane-switch.py` 现在调用 `pane layout --current`，按 rectangle 的 `(y, x)`
   排列 pane，不再依赖 pane ID 或创建顺序。
3. Python 切换器现在优先使用注入的 `HERDR_SOCKET_PATH`，并对 CLI、socket 和 focus
   响应错误给出可见的非零结果。

### Issue #3 已实施

1. `hopen.sh` 和 `hopen-once.sh` 现在用 workspace ID 和视觉位置生成符合 Herdr
   约束的唯一 agent name；过长名称保留可读前缀并附短 hash。
2. canonical kind 原样交给 `herdr agent start --kind`，只保留 `op | cc | cd` 三个
   便利 alias；不再用 `command -v` 预检 executable。
3. 所有 agent start 和初始 prompt 都会尝试；失败诊断写入 stderr，成功布局仍返回
   `ws=<id> panes=<ids...>`，并只发送一次 `Recipe ready` 或失败通知。

### Issue #4 已实施

1. `config/agent-catalog.json` 记录 Herdr 0.8.2 的 22 个 start kinds、candidate
   executables、可选 integration targets 和 repo aliases。
2. `scripts/agent-audit.py` 先检查 Herdr 最低版本，再 best-effort 解析 help，分别报告
   executable 与 integration 状态；help/status 不可读时只警告并继续。
3. `install.sh` 在写入用户配置前要求 Herdr 0.8.2+，安装后执行只读 audit；catalog
   不是 runtime allowlist，也不会执行 integration install。

### 后续待处理问题
1. ~~文档和配置只列出少量别名，但这不限制 canonical full kind：`_resolve_kind` 的默认
   分支本来就会原样透传。需要保留的便利别名只有 `op | cc | cd`。~~ 已处理：
   `scripts/hopen.sh` 的 `_resolve_kind` 只保留 `op | cc | cd` 三个便利别名，
   canonical kind 仍走 `_resolve_kind` 默认分支的原样透传。
2. ~~当前没有 Herdr 最低版本门禁，旧 CLI contract 可能再次静默进入脚本。~~ 已处理：
   `scripts/agent-audit.py` 的 `--check-version` 与 `install.sh` 的版本门禁
   组合使用，强制最低 Herdr 版本要求。
3. ~~installer 会原地更新用户的 `config.toml`，但不会创建该文件的 `.bak`；这是需要
   明确的备份风险，不应写成无条件的实现优点。~~ 已处理：`install.sh` 的
   `backup_if_exists()` 现在在 install / uninstall 两条路径都会先把 `config.toml`
   备份为 `config.toml.bak.YYYYMMDDHHMMSS`（同秒重跑追加 `-1` / `-2` 后缀避免
   覆盖历史备份）。

## 4. 已确认的产品边界

herdr-recipes 保持 **Recipe Launcher**，并允许与外部 workflow 工具组合。

- Recipe 是布局、agent 派位和可选初始 prompt 的可复用描述；创建布局和提交 dispatch
  由 Recipe Launcher 负责。
- Recipe Launcher 不负责等待 agent 工作完成、依赖调度、重试、workflow 恢复或结果收集。
- **Recipe Ready** 表示布局创建成功、配置的 agents 已启动且初始 prompts 已提交；不表示
  agents 已完成任务。
- Workflow 能力优先复用 herdr-workflows 等现有工具。当前只提供文档组合示例，不增加
  运行时依赖；未来出现记录在案的新需求时可继续开发。
- 暂不把项目迁移成 Herdr plugin；继续作为个人配置仓库维护。

完整的 YAML DAG、worktree scheduler、retry/resume/collect/cleanup 不进入近期实现。
这些能力与现有生态高度重叠，也会把当前 launcher 扩张成独立调度器。

## 5. 已确认的实现方向

### Pane 导航（issue #2）

- 删除废弃的 Shell 切换器。
- Python 切换器读取 `HERDR_SOCKET_PATH`。
- `prefix+N` 使用视觉阅读顺序：从上到下、从左到右。

### Agent Dispatch（issue #3）

- 已删除 `command -v "$kind"` 门禁，以 `herdr agent start` 为运行时唯一权威。
- canonical full kind 与 Herdr 保持一致；保留 `op | cc | cd` 三个便利别名。
- agent name 包含安全化 workspace ID 和 position；超长时截断并加入短 hash。
- prompt 成功提交后立即返回，不等待 agent 工作完成。
- 所有 agent/prompt 都尝试后再汇总结果；失败 pane 留在 shell，成功 pane 不受影响。
- 只要 workspace 和 layout 成功，命令整体仍成功，stdout 保持
  `ws=<id> panes=<ids...>`；逐项诊断写 stderr。

### 通知

- 全部成功时发送一次 **Recipe Ready**，使用 `--sound done`。
- 任一配置的 agent 启动或 prompt 提交失败时，发送一次失败汇总，使用
  `--sound request`。
- 配置了 kind 但 executable 缺失属于失败，不作为正常跳过隐藏。
- 通知描述的是 recipe setup/dispatch 结果，不声称 agent task 已完成。成功通知使用
  `done` 仅表示通知声音选择，不改变 **Recipe Ready** 的语义，也不表示 agent lifecycle
  进入了 `done`。

### 安装检查与 catalog（issue #4）

- 已要求最低 Herdr 0.8.2。
- 已从当前 `agent start --help` best-effort 提取 supported kinds。
- 已维护版本化 catalog，记录 known kind、candidate executable、可选 integration target
  和 repo aliases。
- 安装时扫描所有当前 supported kinds，分别显示 executable 和 integration 状态。
- 当前 Herdr 新增但 catalog 未知的 kind 显示 `availability unknown`，仍允许原样配置和
  运行；catalog 永远不是运行时白名单。
- help 解析失败时警告并退回 catalog，不中断 installer。
- audit 只报告，不自动安装 integration。

## 6. 下一步与验证边界

建议按以下顺序实施：

1. 固化本研究记录、领域词汇和 Recipe/Workflow 边界 ADR。
2. 删除旧切换器，实现 socket/caller context 与视觉顺序修复。已完成。
3. 修复 agent name、启动权威、prompt 错误和汇总通知。已完成。
4. 加入版本门禁、agent catalog 与 install audit。已完成。
5. 增加外部 workflow 组合示例，并完成 focused tests 与真实 Herdr smoke。已完成。

### Issue #2 真实 smoke（2026-09-04）

- Herdr 0.8.2 中创建的 `21` 布局 `w55` 按真实 rectangle 排序为
  `w55:p1 → w55:p2 → w55:p3`；在 `w55:p1` 运行切换器索引 `2` 返回成功，随后
  `pane layout` 的 `focused_pane_id` 为 `w55:p2`。
- 创建的 `22` 布局 `w56` 按真实 rectangle 排序为
  `w56:p1 → w56:p2 → w56:p4 → w56:p3`；在 `w56:p1` 运行索引 `3` 返回成功，随后
  `focused_pane_id` 为 `w56:p4`。
- smoke workspace 已关闭；后续 issue #3 smoke 清理完成后，原 workspace `w51` 与
  `focused_pane_id=w51:p2` 已恢复。

### Issue #3 真实 smoke（2026-09-04）

- 同一 `11` Recipe 连续创建 `w57` 和 `w58`，四个 live Pi agent 名称分别为
  `hopen-w57-left/right` 与 `hopen-w58-left/right`，均无碰撞并进入 idle。
- 使用 unknown kind 和 `pi` 创建 `w59` 时，Herdr 的 `unsupported interactive agent kind`
  诊断写入 stderr，命令仍返回 0；`w59:p1` 保留为 shell，`w59:p2` 的
  `hopen-w59-right` Pi agent 成功启动并 idle，证明后续 pane 仍会尝试。
- `w57`、`w58`、`w59` 已关闭，原 workspace `w51` 与 `focused_pane_id=w51:p2` 已恢复。

### Issue #4 真实 audit（2026-09-05）

- 当前 Herdr `0.8.2` 通过最低版本检查，audit 扫描 help 返回的全部 22 个 kinds。
- 当前主机 executable 与 integration 状态分别报告；例如 `cursor` 找到
  `cursor-agent` 但 integration 为 missing，`pi`/`claude`/`codex` 为 current。
- audit 只执行 `herdr integration status`，未执行任何 `herdr integration install`；
  缺失项以 warning/report 表示，不阻断安装。

### 五 pane 布局 smoke（2026-09-19）

- `221` 和 `122` 在 Herdr 0.9.1 上真实创建并按 rectangle 核对了 geometry：
  `221 = left-top → middle-top → right → left-bottom → middle-bottom`；
  `122 = left → middle-top → right-top → middle-bottom → right-bottom`。
  临时 workspace 已清理。布局构建新增 `PANE<n>` parent token，允许从任意已创建 pane 分裂。

### 九布局 smoke matrix（Herdr 0.9.1，2026-09-19）

- 全部 9 个布局在 Herdr 0.9.1 上真实创建并按 rectangle `(y, x)` 排序核对（ws id /
  rect 见每条；rect 格式 `(x, y, w, h)`）。临时 workspace 已全部关闭，开跑前后
  `herdr workspace list` 完全一致。
  - `11`（ws=w5X）：`p1 → p2`；p1 rect=(0,0,76,44)，p2 rect=(76,0,75,44)。
  - `12`（ws=w5Y）：`p1 → p2 → p3`；p1=(0,0,76,44)，p2=(76,0,75,22)，p3=(76,22,75,22)。
  - `21`（ws=w5Z）：`p1 → p2 → p3`；p1=(0,0,76,22)，p2=(76,0,75,44)，p3=(0,22,76,22)。
  - `22`（ws=w50）：`p1 → p2 → p4 → p3`；p1=(0,0,76,22)，p2=(76,0,75,22)，p4=(0,22,76,22)，p3=(76,22,75,22)。
  - `13`（ws=w61）：`p1 → p2 → p3 → p4`；p1=(0,0,76,44)，p2=(76,0,75,22)，p3=(76,22,75,11)，p4=(76,33,75,11)。
  - `31`（ws=w62）：`p1 → p2 → p3 → p4`；p1=(0,0,76,22)，p2=(76,0,75,44)，p3=(0,22,76,11)，p4=(0,33,76,11)。
  - `111`（ws=w63）：`p1 → p2 → p3`；p1=(0,0,76,44)，p2=(76,0,38,44)，p3=(114,0,37,44)。
  - `221`（ws=w64）：`p1 → p2 → p3 → p4 → p5`；p1=(0,0,76,22)，p2=(76,0,38,22)，p3=(114,0,37,44)，p4=(0,22,76,22)，p5=(76,22,38,22)。
  - `122`（ws=w65）：`p1 → p2 → p3 → p5 → p4`；p1=(0,0,76,44)，p2=(76,0,38,22)，p3=(114,0,37,22)，p5=(76,22,38,22)，p4=(114,22,37,22)。
- `prefix+alt+1..9` 与 `prefix+ctrl+1..9`（--no-agents）已绑到以上 9 个代号；
  `prefix+1..6` 仍走 `herdr-pane-switch.py`。
- catalog 与 0.9.1 漂移：0.9.1 新增 `letta` 与 `muse`，已在 `config/agent-catalog.json`
  bump 到 catalog v2 并被 `tests/test_agent_audit.py` 锁住；`letta` 在
  `herdr integration install --help` 的 18 个 target 列表里并标 `(experimental)`，
  `muse` 不在 integration target 列表中（详见 §2.2）。

### 九布局 smoke matrix（Herdr 0.9.5，2026-10-19，核销 issue #10）

在 Herdr 0.9.5 上复跑 `scripts/hopen.sh <code> -n` 全 9 布局，与 0.9.1 记录逐条比对。
**#4123（`pane split` 无目标时改为分裂「调用者」pane）对本仓 `_steps_for` 无影响** ——
`_h_split` 永远显式传 target，证据是 9 个布局的 pane 数与 `(y,x)` 排序后的自然顺序
与 0.9.1 记录完全一致。rect 格式 `(x, y, w, h)`；本次客户端区域为 `120x40`
（0.9.1 记录时为 `152x44`，绝对值不同属正常，只比对相对结构）。ws id 为开跑时
自动分配的连续 id，已全部关闭并核销。

| 代号 | pane 数 | 创建顺序 → 按 `(y,x)` 排序的自然顺序 | 与 0.9.1 |
|---|---|---|---|
| `11` | 2 | `p1 → p2` | 一致 |
| `12` | 3 | `p1 → p2 → p3` | 一致 |
| `21` | 3 | `p1 → p2 → p3` | 一致 |
| `22` | 4 | `p1 → p2 → p4 → p3` | 一致 |
| `13` | 4 | `p1 → p2 → p3 → p4` | 一致（几何已修，见下） |
| `31` | 4 | `p1 → p2 → p3 → p4` | 一致（几何已修，见下） |
| `111` | 3 | `p1 → p2 → p3` | 一致 |
| `221` | 5 | `p1 → p2 → p3 → p4 → p5` | 一致 |
| `122` | 5 | `p1 → p2 → p3 → p5 → p4` | 一致 |

### 三 pane 列的几何修正（2026-10-19，复核时发现）

复跑时对照文档里的 `[A][B/C/D]` 视觉图，发现 `13` / `31` 的实际几何与图不符：

```text
修正前（120×40）：  13: p1 60x40 | p2 60x20 | p3 60x10 | p4 60x10   ← 50% / 25% / 25%
                    31: p1 60x20 | p2 60x40 | p3 60x10 | p4 60x10   ← 同上
修正后（120×40）：  13: p1 60x40 | p2 60x13 | p3 60x14 | p4 60x13   ← 三等分（误差 1 行）
                    31: p1 60x13 | p2 60x40 | p3 60x14 | p4 60x13   ← 同上
```

成因是「连续两次对半切」：第一次切走一半，第二次再对半切剩下的一半，于是
得到 50 / 25 / 25。这与其它布局的视觉契约冲突——`122` 的两列各 2 个 pane
是等分的，`221` 的前两列也是等分的，只有 `13` / `31` 破了「每列等分」。

修法用 Herdr 的 `pane split --ratio`（实测语义是**被分裂的 pane 保留**的比例：
40 行 `--ratio 0.333` → 目标 pane 留 13 行、新 pane 拿 27 行）：先切走 1/3
（`0.333`），再把剩下的 2/3 对半切（`0.5`）。`_steps_for` 因此从两列变成三列
`<parent> <direction> [ratio]`。`13` / `31` 用 ratio 等分三 pane 列；`221` 的
列宽比例修正也使用 ratio，见下节，其余布局不传。
**创建顺序不变**（仍是 top → mid → bottom），所以 `_position_for`、
`_h_row_major` 和三个契约测试都不用动。

### 五 pane 布局的左右分区（2026-10-19）

确认的布局意图是：一个通高 pane 占宽度 50%，旁边的 2×2 四宫格占另外 50%。
`221` 左四宫格、右通高 pane，列宽 25/25/50；`122` 左通高 pane、右四宫格，
列宽 50/25/25。上面的历史 smoke 记录保留原始坐标，其中旧 `221` 的四宫格
占宽度 75%，不符合这次确认的意图。

`221` 现在先按 0.25 分出左列，再把剩余区域按 1/3 分成中列和右列；pane
创建顺序、位置名与派位不变。`122` 的分裂步骤保持原样。几何回归检查覆盖
120×40、124×44、123×41，偶数可整分尺寸精确匹配，奇数尺寸允许 1 格取整误差。

本机 Herdr 0.9.5 实测（120×40，直接调用 `_h_build_layout`，无 agent、未切焦点）：

| 布局 / 临时 workspace | 按创建顺序的 rectangle `(x,y,w,h)` |
|---|---|
| `221` / `w7T` | p1=(0,0,30,20)，p2=(30,0,30,20)，p3=(60,0,60,40)，p4=(0,20,30,20)，p5=(30,20,30,20) |
| `122` / `w7V` | p1=(0,0,60,40)，p2=(60,0,30,20)，p3=(90,0,30,20)，p4=(90,20,30,20)，p5=(60,20,30,20) |

两种布局的通高 pane 与四宫格各占 60 列；临时 workspace 已关闭，并用
`workspace list` 确认 `w7T` / `w7V` 均已不存在。其余两种终端尺寸是模型回归检查，
未进行真实终端 resize smoke。九布局派位及两个启动入口的一致性检查通过。

### 回滚路径的收尾（2026-10-19 实测更正）

`hopen.sh` 在 split 失败时原本先逐个 `herdr pane close` 再 `herdr workspace close`。
实测：**关闭一个 workspace 的最后一个 pane 会连带销毁 workspace**，
紧接的 `workspace close` 必然返回 `workspace_not_found`。所以那次调用永远失败、
只是被 `|| true` 吞掉。现在改成直接 `herdr workspace close`（实测会连同所有
pane 一起关），失败时才逐 pane 兜底。

同时更正一条上游判断：0.9.4 #5143 引入的 `ui.confirm_close` **只影响 TUI 手动关闭**，
CLI 路径（`herdr pane close`）不弹确认。之前按 release notes 推断「回滚可能因确认
弹窗失败而泄漏 workspace」是不成立的。

### `--help` 不再靠写死行号（2026-10-19）

`hopen.sh` 曾用 `sed -n '2,19p'` 打印头部注释，而头部早已长到 31 行——
后半段用法说明和 6 条设计原则被静默截断；`hopen-once.sh` 的 `'2,71p'` 同样少两行。
改成 awk「从第 2 行起连续打印 `#` 行」，以后加注释不会再失配。

- `hopen-once.sh -l <code> -n` 抽查 `22` / `122` / `221`，rect 与 `hopen.sh` 同码结果
  逐坐标一致 —— 两条路径共用 `_h_split` 与 `_h_build_layout`，结论可互相覆盖。
- 跑完 `herdr workspace list` 与开跑前完全一致，临时 ws 全部清理。
- 三个顺带核销项：
  - `prefix` 数组（0.9.2 #4653）：`install.sh` 的 `has_prefix`（`^\s*prefix\s*=`）
    对 `prefix = ["cmd+b", "ctrl+space"]` 仍匹配，不会被 bootstrap 覆盖。
  - `herdr integration list` 的 install target 仍是 **18** 个（含 `letta`，
    `muse` 不在其中），catalog v2 无需 bump。
  - `[session] startup_per_agent_delay_ms` 只作用于**恢复**的 agent；`hopen` 开的是
    新建 pane，不走恢复路径，故默认 100ms 不影响批量 dispatch。

### Issue #5 release-candidate smoke matrix（2026-09-07）

- Herdr 0.8.2 真实布局矩阵已完成，按 rectangle `(y,x)` 排序得到：
  `11 = left → right`；`12 = left → right-top → right-bottom`；
  `21 = left-top → right → left-bottom`；`22 = left-top → right-top → left-bottom → right-bottom`；
  `13 = left → right-top → right-mid → right-bottom`；
  `31 = left-top → right → left-mid → left-bottom`；
  `111 = left → middle → right`。Smoke workspace `w5A`–`w5G` 已全部清理。
- Duplicate-name、成功 `done` notification、partial failure `request` notification 和
  later-pane retention 证据沿用 issue #3 的真实 `w57`/`w58`/`w59` smoke；当前 host
  audit 为 Herdr 0.8.2 read-only，未执行 integration install。
- 最终 verification 分层记录为：fixture/full tests、Python/shell static checks、
  catalog/install audit fixtures、真实 Herdr smoke；未引入 Workflow runtime 依赖。

生产实现必须分别验证脚本静态检查、fixture/mock contract，以及真实 Herdr pane 几何、
重复布局、部分 agent 失败和通知行为。仅有 shell syntax 通过不能证明运行时契约成立。


## 7. v0.8.0 收尾验证（2026-10-19，issue #13）

### 测试去重与失败回滚

`unittest` 原先发现 106 项，其中 48 次是测试类继承带来的重复执行。现在共用
不包含 test 方法的 fixture，保留 58 项原有用例，增加 3 项回滚用例；共 61 项，
没有继承重复收集。未将 Python fake 改写为 shell，先消除可证明的重复工作。

同机全量基线：106 项通过，84.903 秒；收尾后：61 项通过，47.445 秒。
减少重复后即低于原先 120 秒窗口，新增回滚覆盖也包含在后者耗时内。
Bash syntax、Python compileall、git diff --check 和四个入口的 0.8.0 版本输出均通过。

新增故障注入覆盖两个入口的四个 split 失败点，断言失败后不再启动 agent、提交
prompt、切换 workspace 或发送 Recipe Ready 通知。workspace close 失败时逐 pane
兜底；最后一个 pane 关闭后不再重复 workspace close；若 pane 清理仍失败，stderr
明确报告“回滚未完成”及 workspace ID。fake 用独立状态文件验证资源是否清理。

### 最低版本与推荐版本的真实运行态

- 最低版本：从官方 [v0.8.2 发布](https://github.com/herdrdev/herdr/releases/tag/v0.8.2)
  下载 `herdr-macos-aarch64` 到临时目录，`--version` 为 `herdr 0.8.2`，其 split help
  已包含 `--ratio`。
- 推荐版本：`/opt/homebrew/bin/herdr`，`herdr 0.9.5`。
- 两者均新建独立 server，使用临时 `HERDR_CONFIG_PATH`、`HERDR_SOCKET_PATH`、
  `XDG_CONFIG_HOME` 和 `XDG_STATE_HOME`；没有修改本机安装、集成或用户 server。
- 每个版本分别通过 `hopen.sh <code> -n` 与 `hopen-once.sh -l <code> -n` 创建
  全部九布局，检查 pane 数、创建顺序对应的 rectangle、比例与 1 格取整容差。
  共 36 次布局检查通过；每次关闭新 workspace，最后与初始 workspace 集合对照，
  再停止独立 server，全部完成清理。

| 版本 | 内容区 | 13/31 的三段高度 | 111 列宽 | 221 列宽 | 122 列宽 | 两入口九布局 |
|---|---|---|---|---|---|---|
| 0.8.2 | (26,1,94,39) | 13/13/13 | 47/24/23 | 24/23/47 | 47/24/23 | 18/18 |
| 0.9.5 | (0,0,120,40) | 13/14/13 | 60/30/30 | 30/30/60 | 60/30/30 | 18/18 |

0.8.2 的 sidebar 与 tab 栏造成内容区偏移，验证按真实 pane 的包围矩形归一化，
未将整个 viewport 错当成 pane 内容区。`111` 保留 50/25/25 的原有行为；`221` 与
`122` 均满足通高 pane 与四宫格各占一半。创建顺序和位置名保持一致。

可重复运行（需要 Bash、Python 3 与 jq；binary 使用已安装或另行下载的路径）：

```bash
python3 -m unittest discover -s tests -v
python3 tests/smoke_herdr_layouts.py --binary /path/to/herdr --output /tmp/herdr-smoke.json
```

本轮没有启动真实模型 agent、验证 agent resume、实按 CJK 输入法快捷键或做 Linux
运行态复验；这些不由布局 smoke 或 fixture 测试替代。
