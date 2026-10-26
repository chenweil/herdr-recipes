# Changelog

本项目所有值得记录的改动都写在这里。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

版本号存在仓库根的 `VERSION` 文件里，所有脚本通过 `-v` / `-V` / `--version` 读同一份。

## [Unreleased]

### 新增

- 团队配置从目标目录向上逐级查找 `.herdr-team.json`，命中即止，规则与 agent 发现
  项目 `.agents/skills` 一致。linked worktree 根目录自带 `.git` 文件，先跳到主
  worktree 根继续找，因此 worktree 里的开发 worker 会继承所属项目的团队策略；遇到
  不带配置的仓库根就停止，不跨仓库继承上层文件。

- 团队配置支持 `skills` 角色到技能的映射（`implement`/`review`/`diagnose`/`route`）。
  已配置的角色按强制处理：派工必须指定该技能，不可用则报告阻塞；未配置的角色按
  建议处理：协调者提议并写进提示词。同一套规则由数据有无决定强弱，不引入模式开关。
  协调者按 worker 实际 kind 解析调用形式并在其可见技能目录下确认真实存在，不假设
  当前对话的可用技能等于 worker 的可用技能。配置校验拒绝未知角色与非技能名值。

### 修复

- 派工后的等待改为事件驱动：协调规则要求在回报格式中约定 `DONE <任务 ID>` 完成标记，
  优先用 `pane wait-output` 等内容就绪信号，否则用 `agent wait --until idle` 阻塞；
  禁止分钟级 `sleep` 盲等，观察型 sleep 单次不超过 60 秒。修复 worker 提前交付而
  协调者因长睡眠未察觉导致的交付延迟（实测一次迟到约 4.5 分钟，原写法最坏可达
  8 分钟以上）。SKILL 必守约束增加对应的第五条。

- 协调者主动监督开发、review 等 worker 的模型/API 异常与停滞，按有界恢复流程继续
  原会话或重建同角色 worker，保留已有改动与交接证据；达到恢复上限后明确报告阻塞。

- issue 完成后由协调者核验交付、存档并主动回收所属临时 tab/pane 与 worker，无需用户
  再次确认；开始下一项前补查收尾。保留主对话、受保护资源、worktree 和分支，核对
  前台进程与关闭结果并记录保留原因，替换原先“交付后默认待命、缺名额才回收”的规则。

## [0.9.0] - 2026-10-26

### 新增

- **跨 tab/worktree 的对话式团队**（ADR-0003）：共用协调模板管理显式归属的资源，
  根据任务选择分工，全团队默认四个 worker、kind 沿用主协调者；本次明确要求可覆盖。
  `hteam-context.sh` 为启动器与原地协调共用配置和上下文，支持项目 JSON、显式配置和
  CLI 默认值覆盖。通过 Herdr 派工、记录本地证据、整合验证与受控复用；约束属于模型
  行为与现场核验，不是执行层硬限制，也不增加后台调度或恢复服务。

- **`herdr-recipes` 项目 skill**（#16）：自然语言选择固定 Recipe、新 hteam 团队、当前
  tab 协调和只读状态；复用全局 herdr skill 的 CLI 知识与现有协调模板。提供项目发现
  链接和用法，原地协调保留其它已有 pane/agent；模型端发现与完整执行需实测。

- **`hteam.sh` 对话式协调入口**（#15，ADR-0002）：仅启动一个主 agent，注入可读
  的协调模板与精确 tab 上下文；主对话决定 worker 数量、布局、待命准备和后续
  提示词委托，并查看与汇总结果。脚本不引入后台队列、自动重试或恢复引擎。

- **`hstatus.sh` 只读 workspace 状态摘要**（#14）：支持调用方上下文、显式
  `--workspace`、文本与 `schema_version=1` JSON；包含普通 shell pane，按精确 ID
  联接一次 agent 查询，缺失字段为 null/unknown。几何完整时按 `(y,x)` 排序，
  多 tab 或几何不可用时告警并降级为完整 pane ID 顺序，全程不改变焦点或配置。
- fixture 覆盖 workspace 隔离、混合状态、caller 解析、错误输出与降级；
  `tests/smoke_hstatus.py` 在隔离 server 验证 caller、显式查询、多 tab 及状态不变。

### 修复

- 固定 Recipe 请求优先走启动器：如“111 布局，每个 pane 开 pi”，直接新建三个 pi 的
  工作区并保留当前协调者，省去角色/命名/是否保留主对话的追问。启动器上下文通过精确
  pane ID 核验，避免 Codex 工具环境未继承 pane ID 时被 `--current` 失败阻断。
  真实模型重放已通过：协调者未收到追问即完成 111 布局 + 三个 pi，协调者工作区保留，
  几何与清理均核验通过。

- `tests/smoke_hteam_recipe.py` 自建临时 `CODEX_HOME`（复制用户 Codex 配置，不改全局）：
  Codex 默认 `workspace-write` 沙箱连不上 Herdr 的 unix socket，pane 内的启动器命令会被
  拒绝，且 CLI 会落到用户真实 Herdr 实例——审批一旦放行就会污染真实环境、断言也必然
  失败。隔离配置覆盖 `approval_policy = "never"` 与 `sandbox_mode = "danger-full-access"`，验收命令现在可在默认环境直接跑通。

- 协调模板明确三个 worker 的分裂顺序（主 pane 向右、右 pane 向下、新下 pane 向右），
  避免形成三层堆叠；汇报布局前必须读取实际 pane 矩形。真实模型 smoke 新增
  `--workers 3`，修复前已复现并拦截几何错误，修复后 pi 主协调者 + 三个 pi worker
  的布局、后续委托与汇总通过。

### 变更

- **协调模板固化两条必守约束**（写进 `scripts/coordinator-prompt.md`，`herdr-recipes`
  skill 中的概要指针同步）：
  - ① 管理/调度角色（协调者与 review 角色）不在自身主对话现场直接运行测试；
    所有测试都在独立 tab/pane 执行，结束后回主现场核对，报错立即终止进程，
    避免局部失败阻塞整个流程与后续派工。
  - ② 每个 issue 的开发独占一个以 issue 标识命名的新 tab，不在既有 issue 的 tab
    上继续开发新 issue，属主与执行记录随 tab 归属，避免不同 issue 的改动与
    上下文混在同一现场。

## [0.8.0] - 2026-10-19

### 修复

- 回滚的逐 pane 兜底不再重复关闭已销毁的 workspace；若清理仍失败，stderr
  明确给出 workspace ID，便于手动处理。两个启动入口均有故障注入回归覆盖。
- **`hopen.sh <code> -n` 里的 `-n` 被静默忽略**。参数解析器遇到第一个非选项
  参数（layout 代号）就 `break`，写在代号后面的选项再也没人解析。头部注释声明的
  用法正是 `bash hopen.sh <code> [--no-agents|-n]`，所以「只开布局不起 agent」这条
  最常用的路径实际上会**真的把 conf 里的 agent 启起来**。现在第一个非选项参数取作
  代号后继续解析选项，第二个非选项参数明确报错。
- **`--help` 靠写死行号截断头部注释**。`hopen.sh` 用 `sed -n '2,19p'`，而头部已长到
  31 行，后半段用法说明和 6 条设计原则被静默吃掉；`hopen-once.sh` 的 `'2,71p'`
  同样少两行。改成 awk「从第 2 行起连续打印 `#` 行」，加注释不会再失配。
- **split 失败后的回滚多一次注定失败的调用**。实测关闭 workspace 的最后一个 pane
  会连带销毁 workspace，紧接的 `herdr workspace close` 必然返回 `workspace_not_found`。
  改成直接 `workspace close`（实测会连同所有 pane 一起关），失败才逐 pane 兜底。

### 变更

- 测试 fixture 与测试用例分离，消除 48 次继承重复执行；原有 58 项用例全部保留，
  新增 3 项回滚测试，覆盖每个 split 失败点、workspace close 兜底及清理失败诊断。
- `111` 保留原有列宽 50% / 25% / 25%，修正文档中的“三列等宽”。
- 官方 Herdr 0.8.2 与本机 0.9.5 均使用独立 server 验证两入口九布局，
  保留 0.8.2 契约下限与 0.9.5 推荐版本。
- **`13` / `31` 的三 pane 列改成真三等分**。原先连续两次对半切，实际几何是
  50% / 25% / 25%，与文档里的 `[A][B/C/D]` 以及其它布局「每列等分」的视觉契约冲突
  （`122` 的两列、`221` 的前两列都是等分的）。改用 Herdr 的 `pane split --ratio`
  （语义是**被分裂的 pane 保留**的比例）：先切走 1/3（`0.333`），再把剩下的 2/3
  对半切（`0.5`）。120×40 下实测 `13` / `31` 从 20/10/10 变为 13/14/13。
  `_steps_for` 的每行从两列变成 `<parent> <direction> [ratio]`；**创建顺序不变**，
  故 `_position_for` / `_h_row_major` 的映射未动。
- **`221` 调整为左四宫格 50% + 右通高 pane 50%**，列宽从 50/25/25 改为
  25/25/50，与 `122`（左通高 pane 50% + 右四宫格 50%）互为镜像。pane 创建
  顺序、位置名与 agent 派位保持一致；几何回归检查覆盖两种布局与奇偶终端尺寸。
- **版本口径拆成「契约下限」与「实测推荐」两档**。`config/agent-catalog.json` 新增
  `recommended_herdr_version`（0.9.5），`min_herdr_version` 仍是 0.8.2 不动。
  `install.sh` 不再硬编码版本号，改为从 catalog 读，报错文案跟着 catalog 走。
  - 低于 min：不兼容，`die`（行为不变）
  - 低于 recommended：只 `warn`，提示升级理由：0.9.4 #5017 重写输入管线后，
    中/日/韩输入法下 prefix 快捷键存在回归，0.9.5 #5255 修复；不据此断言
    所有低版本或所有输入法环境都必然失败。
  - 不把 min 抬到 0.9.5，是为了不挡住仍在 0.8.x 的用户；详见
    `docs/review-2026-10-19.md` §P0-4
- **在 Herdr 0.9.5 上复跑九布局 smoke matrix**（核销 issue #10）。0.9.1 的
  `pane split` 目标语义变化（#4123）**未影响 `_steps_for`**：`_h_split` 始终显式
  传 target，9 个布局的 pane 数与按 `(y,x)` 排序的自然顺序与 0.9.1 记录逐条一致。
  细节见 `docs/herdr-research.md` 的「九布局 smoke matrix（Herdr 0.9.5）」节。
- `docs/herdr-research.md` 头部验证环境补上 0.9.5，状态行补 #10 已核销。
- 更正一条上游判断：0.9.4 #5143 的 `ui.confirm_close` 只影响 TUI 手动关闭，
  CLI 路径不弹确认（见「修复」小节的回滚条目）。
- **`scripts/hopen.sh` 中重复的 conf 解析函数合并为 `_read_section_key`**：
  `_kind_for` / `_prompt_for` / `_pane_name_for` 原先各自复制
  `sed + grep + sed` 逻辑，现在统一调用共享 helper。顺带受益：提取正则
  统一为 `([^"]*)`（在第一个 `"` 处严格截断），消除了 `_prompt_for`
  原本使用的 `(.*)` 宽松正则会听任 stray 双引号干扰的隐性不一致。
  后续添加 conf 字段现在只改一处。
- **`scripts/README.md` 新增「Agent name 格式」章节**：
  把 `hopen.sh` 的 `_agent_name` 函数中 32 字符截断 + cksum hash 规则
  写到 `herdr agent list` 里能看到的 agent name 含义上，以避免下次看到
  `hopen-billing-t-0001--a8f3c2` 这样的名字时还需要读函数源码才能判断
  它属于哪个 pane。

### 新增

- `tests/smoke_herdr_layouts.py`：可指定 Herdr 二进制的隔离 smoke，验证两入口
  九布局的 pane 数和几何；不启动 agent，自动清理临时 workspace 与 server。
- **`tests/test_hopen_dispatch.py::LayoutVisualOrderContractTests`**：
  三个契约测试覆盖所有 9 个 layout。
    - `test_layout_data_table_contract`：静态校验 `_panes_for` /
      `_position_for` / `_h_row_major` 表数据无重复、集合一致
    - `test_hopen_once_dispatch_lands_on_visual_positions_for_all_layouts`：
      `hopen-once.sh` 按视觉阅读顺序派位，填补 12 / 13 / 111 三种 layout
      在该契约上无专门覆盖的缺口
    - `test_hopen_and_hopen_once_kind_to_pane_mapping_match_for_all_layouts`：
      `hopen.sh` 用 conf 与 `hopen-once.sh` 用 CLI 两种入口，对同一 layout
      给出一致的 kind → pane 映射。背后的逻辑保证三个上下文
      （创建顺序、视觉阅读顺序、conf 位置名）之间不会出现需要手动同步的副本。
- **`tests/test_agent_audit.py::ResolveKindCatalogSyncTests`**：
  两个测试锁定 `hopen.sh::_resolve_kind` 与 `config/agent-catalog.json::aliases` 同源。
    - `test_hopen_resolve_kind_matches_catalog_aliases`：跑 bash source，遍历
      catalog 中所有 alias → 验证 `_resolve_kind` 输出与 JSON 一致
    - `test_hopen_resolve_kind_passes_through_unknown`：
      任何未在 alias 表里的输入（包括 `codex` / `pi` / `hermes` 本身）原样透传。
  同源之后后续接透 catalog 作为运行时权威只需要改 JSON，但为安全起见 bash 仍保留
  原表作为兜底。

## [0.7.0] - 2026-09-20

### 新增

- **`prefix+alt+8` / `prefix+alt+9` 与对应的 `prefix+ctrl+8/9`（带 `--no-agents`）
  分别绑到 `hopen.sh 221` / `hopen.sh 122`**。`prefix+alt+1..9` 依次打开 `12 21 22 13 31 111 11 221 122`，
  `prefix+ctrl+1..9` 同样代号但起 bare pane，`prefix+1..6` 仍走
  `herdr-pane-switch.py`。`config/keys.toml`、`install.sh` 的 `legacy_keys`
  正则、`README.md`、`scripts/README.md`、`scripts/hopen.sh` 的对应行已同步
  到 1..9；新写 `tests/test_keys_toml.py` 用 `tomllib` 锁住 alt/ctrl 1..9 映射
  与头部注释，`tests/test_agent_audit.py` 加了 isolation 用例验证旧手工
  `prefix+alt+8` 升级时被清掉、managed block 接管。
- **9 布局 smoke matrix 已在 Herdr 0.9.1 上重跑**，按 rectangle `(y, x)` 排序核对了
  `11 12 21 22 13 31 111 221 122`；结果与 `herdr-pane-switch.py` 的 `(y, x)` 顺序及
  #5 矩阵一致（22 与 122 的末两位与 `(y, x)` 顺序不同是 #5 已记录的既有行为，
  `hopen-once.sh` 的 `_h_row_major` 表由另开 issue 决定）。记录在
  `docs/herdr-research.md` 的"九布局 smoke matrix"节，含真实 ws id 与 pane rect。
- **`config/agent-catalog.json` 已 bump 到 catalog v2**：补齐 Herdr 0.9.1 新增的
  `letta` 与 `muse` 两种 kind。`letta` 在 `herdr integration install --help` 的
  18 个 target 列表中（含 `(experimental)` 后缀），`integration_targets` 保留
  `["letta"]`；`muse` 不在 integration target 列表中，`integration_targets` 留空。
  `scripts/agent-audit.py::parse_integration_status` 已剥离 ` (...)` 后缀，
  并加测试锁住行为。

关闭 #8。

### 变更

- **`scripts/hopen-once.sh` 的 `21` / `31` kind 派位改为 `(y, x)` 视觉顺序**，
  与 `prefix+N` 数字切换器以及 smoke 矩阵一致。第 2 个 kind 在 `21` 上从 `left-bottom`
  变为 `right`，在 `31` 上从 `left-mid` 变为 `right`；`31` 的第 3 个 kind 从 `left-bottom`
  变为 `left-mid`。属于行为变更：`hopen-once.sh -l 21 a b c` / `-l 31 a b c d`
  调用现有脚本的用户，第 2 / 第 3 个 kind 落到的 pane 与之前不同。
  `hopen.sh` 的 `hopen-agents.conf` 派位路径按位置名寻址，不受影响。
  `README.md`、`scripts/README.md` 与本仓库其它文档里的 21 / 31 位置名示例同步改为视觉顺序。

关闭 #9。

## [0.6.1] - 2026-09-19

### 修复

- **installer 在原地写 `config.toml` 之前会创建 `.bak.YYYYMMDDHHMMSS` 备份**。
  install 与 uninstall 两条路径都先备份；用 `cp -p` 保留权限与时间戳。
  同一秒内重复运行时备份名追加 `-1` / `-2` 后缀，不覆盖历史备份。
  与现有 `scripts/` 目录备份使用同一命名约定，用户已有的非 managed 内容不再
  有被无声覆盖的风险。关闭 #7。

### 文档

- 修正过期的 alias、版本号和 `--kind` 引用。

## [0.6.0] - 2026-09-19

### 新增

- **`221` 和 `122` 布局**（五 pane，三列）。`221` 是 `[A/B][C/D][E]`，`122` 是 `[A][B/C][D/E]`。
  - `hopen.sh 221` / `hopen-once.sh -l 221` 都认，`122` 同理；`hopen-once.sh` 不传 `-l` 时
    5 个 kind 仍要求显式指定布局
  - 位置名：`221` 用 left-top / left-bottom / middle-top / middle-bottom / right，
    `122` 用 left / middle-top / middle-bottom / right-top / right-bottom，
    `hopen-agents.conf` 里按 `[layout.221.panes.<位置名>]` 配置
  - `_steps_for` 新增 `PANE<n>` parent token，可从任意已创建的 pane 分裂；
    两个布局的创建顺序与视觉顺序不同，脚本负责映射
  - 未改 `config/keys.toml`，没有新增快捷键
- 测试覆盖 `221` / `122` 的 split 顺序、视觉顺序派位和 conf 位置名派位；
  Herdr 0.9.1 真实创建并核对了两个布局的 geometry。

现在全部 9 个布局：`11` `12` `21` `22` `13` `31` `111` `221` `122`。

## [0.5.0] - 2026-09-07

### 变更

- 数字 pane 切换现在读取当前布局的 rectangle，按从上到下、从左到右的视觉顺序工作，
  并支持注入的 `HERDR_SOCKET_PATH`。
- 移除废弃的 Shell 数字切换器，保留 Python 作为唯一实现。
- Agent Dispatch 使用包含 workspace 和视觉位置的唯一名称，直接交给 Herdr 处理
  canonical kind，并汇总 start/prompt 失败后发送一次结果通知。
- Installer 要求 Herdr 0.8.2+，加入 versioned agent catalog 和只读 executable/integration
  audit；未知 kind 仅报告 `availability unknown`，不自动安装 integration。
- 补充与外部 Workflow 组合的最小文档示例，并记录七布局和 release-candidate smoke matrix。

## [0.4.0] - 2026-09-02

### 新增

- **`11` 布局**（`[A][B]` 左右对半）。之前最少只能开 3 个 pane，现在 2 个也行。
  - `hopen.sh 11` / `hopen-once.sh -l 11` 都认
  - `hopen-once.sh` 不传 `-l` 时，**2 个 kind 自动选 11**（原来 3 → 12、4 → 22）
  - 位置名是 `left` / `right`，`hopen-agents.conf` 里用 `[layout.11.panes.left]`
  - 新键位：`prefix+alt+7` 开 11（带 agent），`prefix+ctrl+7` 开裸布局

现在全部 7 个布局：

| 代号 | pane 数 | 视觉 |
|---|---|---|
| 11 | 2 | `[A][B]` |
| 12 | 3 | `[A][B/C]` |
| 21 | 3 | `[A/B][C]` |
| 111 | 3 | `[A][B][C]` |
| 13 | 4 | `[A][B/C/D]` |
| 31 | 4 | `[A/B/C][D]` |
| 22 | 4 | `[A/B][C/D]` |

### 变更

- `install.sh` 托管的键位范围从 `prefix+(alt|ctrl)+1..6` 扩到 `1..7`。
  升级时会把旧的 `+7` 绑定（如果你自己加过）一并清掉再重建。

## [0.3.0] - 2026-08-28

### 新增

- **Pane 命名**。`hopen.sh` 和 `hopen-once.sh` 现在会给每个 pane 设置 label，
  底层调 `herdr pane rename`。两个配置入口：
  - `hopen-agents.conf` 加 `pane_name = "..."`（按 layout 的视觉位置分段）
  - `hopen-once.sh` 加 `-N` / `--pane-name`，三种写法都支持：
    重复 flag（`-N A -N B -N C`）、逗号分隔（`-N "A,B,C"`，自动 trim 空白）、
    位置参数溢出（`-l 21 pi pi codex pi-top pi-bot cd-right`，超出 pane 数的
    位置参数当 NAMES）
  - 缺省回退到视觉位置名（`left-top` / `right-bottom` / `middle` 等），
    所以所有 pane 都有可读的名字
  - rename 失败只写 stderr，不影响布局和 agent 启动
- **Workspace / Tab 自动命名**。新 workspace 和它的 tab 按 cwd 推导名字：
  git repo 用当前分支名，非 repo 用 `basename`。撞名时加 `+a` / `+b` 后缀，
  26 个用完退化成时间戳。
- **版本号**。`VERSION` 文件 + `scripts/version.sh` 作为单一读取入口。
  `hopen.sh`、`hopen-once.sh`、`herdr-pane-switch.py`、
  `install.sh` 都支持 `-v` / `-V` / `--version`。
- 本 CHANGELOG。

### 变更

- `_panes_for` 从 `hopen-once.sh` 移到 `hopen.sh`，两个脚本共用同一个查表。
- `_h_build_layout` 参数扩展到 5 个：`layout`、`ws_label`、`cwd`、`tab_label`、
  `pane_names`。后两个是新增，留空则走默认推导。
- `hopen-once.sh` 的 kind 数超出 layout pane 数时，多余部分不再被丢弃，
  而是当 pane name 用。

## [0.2.0] - 2026-08-27

### 新增

- **`hopen-once.sh`**：命令行临时布局，bypass `hopen-agents.conf`。
  layout 按 kind 数量自动选（3 → 12，4 → 22）或用 `-l` 显式指定；
  kind 按视觉阅读顺序（左→右、上→下）落到 pane。
- `-k K:N` 语法批量重复同一个 kind，`-k codex:4` 等价于打四次 `-k codex`。
- `-C` / `--path`：指定新 workspace 的 cwd，支持绝对路径、相对路径和 `~`。
- `-p` / `--prompt`：按索引给每个 pane 发初始 prompt。

### 变更

- 短 flag `-N` 从 `--no-agents` 上摘掉，`-n` 成为唯一写法
  （`-N` 在 0.3.0 里被 `--pane-name` 接手）。

## [0.1.0] - 2026-08-27

### 新增

- **`hopen.sh`**：按代号（`12` / `21` / `22` / `13` / `31` / `111`）开 pane 布局，
  开在新 workspace 里，不影响当前 ws。split 失败会回滚已开的 pane 并关掉 ws。
- **`hopen-agents.conf`**：按 layout + 视觉位置配置每个 pane 起哪个 agent，
  可选带 prompt。conf 缺失 / 段缺失 / kind 没装都只跳过对应 pane，不影响布局。
- **Kind 别名**：`op` → `opencode`、`cc` → `claude`、`cd` → `codex`、`pi` → `pi`；
  未列出的原样透传，herdr 新增 kind 时不用改代码。
- **数字 pane 切换器**：切到当前 workspace 的第 N 个 pane。
- **`config/keys.toml`** + **`install.sh`**：一条命令装好键位。
  `prefix 1..6` 切 pane，`prefix alt 1..6` 开布局并起 agent，
  `prefix ctrl 1..6` 开裸布局。install 幂等，用 marker 块管理 `config.toml`，
  块外的配置不动；支持 `--uninstall` 回滚。
