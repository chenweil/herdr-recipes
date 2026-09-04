# Herdr 调研与验证记录

> 调研日期：2026-09-03
> 验证环境：Herdr 0.8.2，macOS
> 方法：官方文档、本机 CLI/schema、仓库源码和社区项目交叉核验
> 状态：事实已复核；产品边界已经讨论确认；issue #2/#3 已完成实现、focused tests 和真实 Herdr smoke

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

### Integration 与恢复边界

- Herdr 可在 server restart 后恢复 workspace/tab/pane 的形状和 cwd。
- 受支持 agent 的 native conversation resume 依赖 current official integration 提供的
  session reference。
- 这不等于恢复 workflow 的 DAG、retry、result 或 cleanup 状态；这些属于 workflow
  runner 自己的持久化协议。
- `integration install` 会写入 Claude、Codex、Pi 等工具的用户配置，因此
  herdr-recipes 只做检查和提示，不自动安装。

## 3. herdr-recipes 当前状态

当前版本为 0.4.0。仓库提供固定 pane layouts、可选 agent 派位、初始 prompt、pane
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

### 后续待处理问题
1. 文档和配置只列出少量别名，但这不限制 canonical full kind：`_resolve_kind` 的默认
   分支本来就会原样透传。需要保留的便利别名只有 `op | cc | cd`。
2. 当前没有 Herdr 最低版本门禁，旧 CLI contract 可能再次静默进入脚本。
3. installer 会原地更新用户的 `config.toml`，但不会创建该文件的 `.bak`；这是需要
   明确的备份风险，不应写成无条件的实现优点。

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

### 安装检查与 catalog

- 最低支持 Herdr 0.8.2。
- 从当前 `agent start --help` best-effort 提取 supported kinds。
- 仓库维护版本化 catalog，记录已知 kind、candidate executables、可选 integration
  target 和 repo aliases。
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
4. 加入版本门禁、agent catalog 与 install audit。
5. 增加外部 workflow 组合示例，并完成 focused tests 与真实 Herdr smoke。

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

生产实现必须分别验证脚本静态检查、fixture/mock contract，以及真实 Herdr pane 几何、
重复布局、部分 agent 失败和通知行为。仅有 shell syntax 通过不能证明运行时契约成立。
