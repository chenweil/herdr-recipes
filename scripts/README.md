# herdr scripts

`config.toml` 里 `[[keys.command]]` 调到的本地脚本都放这里。

## version.sh — 版本号读取

版本号存在仓库根的 `VERSION` 文件，`version.sh` 是唯一读取入口，
被 `hopen.sh` / `hopen-once.sh` / `install.sh` source；
`herdr-pane-switch.py` 与 `hstatus.py` 自己读同一个文件。发版只改 `VERSION` 一处。

```bash
hopen.sh -v                 # hopen 0.9.0
hopen-once.sh --version     # hopen-once 0.9.0
```

安装后 `~/.config/herdr/scripts` 是指向仓库的 symlink，所以 `_hr_version` 用
`pwd -P` 解析真实路径再拼 `../VERSION`；用逻辑路径会落到
`~/.config/herdr/VERSION`（不存在）。读不到时输出 `unknown` 而不报错。

每个版本改了什么看仓库根的 [CHANGELOG.md](../CHANGELOG.md)。

## agent-audit.py — 只读 agent catalog audit

`agent-audit.py` 要求 Herdr `0.8.2+`（推荐 `0.9.5`；低于推荐版本只警告不拦截），解析当前 `herdr agent start --help` 的全部
canonical kinds，并读取 `herdr integration status`。它根据
`config/agent-catalog.json` 分别报告 executable 的 `found`/`missing`/`unknown` 和
integration 的 `current`/`missing`/`outdated`/`N/A` 状态。

catalog 只是安装审计和可读性报告，不是 runtime allowlist；未知 kind 显示
`availability unknown` 后继续。审计不会调用 `herdr integration install`。

```bash
python3 scripts/agent-audit.py
```

## herdr-pane-switch.py — 按视觉顺序切换 pane

脚本调用 `herdr pane layout --current` 获取调用方所在布局，按 rectangle 的
`(y, x)` 从上到下、从左到右排列 pane，再通过当前 session socket 用精确的
`pane_id` 执行 focus。优先使用 `HERDR_SOCKET_PATH`；未注入时回退到默认本地
session socket。布局响应、focus 响应、socket 错误和越界索引都会以可见的非零
错误返回。

## hteam.sh — 对话式协调入口

```bash
hteam.sh                          # 只启动一个 codex 主协调者，等待任务
hteam.sh -k pi                     # 主协调者和默认 worker 使用 pi
hteam.sh --worker-kind pi --max-workers 3 -p "调研这个问题，汇总文档"
hteam.sh -C /path/to/project -p "完成需求，安排并行实现与联合验证"
hteam.sh --config /path/to/team.json --no-focus
```

`-k` 选择主协调者。启动器仍只创建一个 workspace/tab/root pane，并启动一个主 agent；
worker 的具体任务和资源由模型决定。完整任务持续推进到交付或真实阻塞；“先准备、
待命”只准备角色。research/review 等专业技能负责方法，所有 worker 委托通过 Herdr。
固定 Recipe 继续走原有入口。

### 配置与原地协调

目标目录的 `.herdr-team.json` 是可选配置，格式见 [示例](../config/hteam.example.json)：

```json
{"worker_kind": null, "max_workers": 4, "skills": {"implement": "implement"}}
```

查找从 `-C` 目标目录向上逐级进行，命中第一个 `.herdr-team.json` 就停止，规则与
agent 发现项目 `.agents/skills` 一致。linked worktree 的根目录自己带 `.git` 文件，
这时先跳到主 worktree 根继续找，所以 worker checkout 会继承所属项目的团队配置。
遇到不带配置的仓库根就停止，不跨仓库继承上层目录的文件。

`worker_kind: null` 或省略表示沿用主协调者 kind；`max_workers` 省略为 4。
`skills` 省略为 `{}`，键只接受 `implement`/`review`/`diagnose`/`route`，值为技能名。
无效 JSON、拼错字段或显式缺失文件在创建资源前报错。`--config FILE` 优先于
`HTEAM_CONFIG`，两者都未给才查目标目录文件；`--worker-kind/--max-workers` 覆盖文件
中的值。kind alias 与 hopen 共用，支持情况最终由 Herdr 判定；没有预先执行各 agent
的模型、账号或权限配置。

`skills` 把角色映射到技能：已配置的角色按强制处理（实现走 `implement`、审查走
`code-review`、排查走 `diagnosing-bugs`，不得用其它方法替代），未配置的角色按建议
处理（协调者提议并写进提示词）。同一个规则按数据生效，需要固定流程的项目在
`.herdr-team.json` 写上映射即可，没写的项目自然退化为建议。技能靠 skillcaddy 等
工具软链到项目级或全局级，脚本不复制也不校验安装；协调者在派工前按 worker 实际
kind 解析调用形式并在其可见技能目录下确认存在。细节与 `route` 的例外处理见
[协调规则第 4 节](coordinator-prompt.md#角色技能约定)。

用户本次明确要求优先于启动配置，启动配置优先于默认值。“这次六个 pi”由模型理解
并临时调整约束，脚本不会解析任务文本或改写配置。上限跨本团队所有 tab/worktree
合计，启动中、工作中、待命均计入，主协调者和普通进程不计入。这是模型行为约定与
现场核验，不是强制资源隔离。数量不足或类型失败须报告，不能静默替换后宣称成功。

当前对话接管时，先通过实际 Herdr 查询取得位置与 kind，然后使用同一 context 入口：

```bash
bash scripts/hteam-context.sh -C /path/to/project \
  --workspace <实际ID> --tab <实际ID> --pane <实际ID> --kind <实际kind>
```

有 live name 才传 `--name`。该入口只输出 JSON，不调用 Herdr、不创建文件或资源。
随后当前 agent 读取 coordinator-prompt.md 并沿用该 context；后续对话不重复生成团队。
新建团队和原地接管共用配置解析、默认值、team_id、record_dir 和工具路径。

### 团队、证据与生命周期

管理范围是本团队创建或用户明确交付的资源。主 tab 保留为用户入口，默认在工作 tab
组织 worker；独立并行代码修改使用有明确 Git 基线的 worktree，对应 Herdr 关联
workspace。已有未提交改动是否影响基线必须明确。用户指定当前 tab 时才读取
[当前 tab 布局](coordinator-layouts.md)，不强制重排已有现场。逐 tab 查询真实矩形，
用显式 ID 与 `--no-focus` 操作，保持用户焦点。

context 的初始 workspace/tab/pane 用于身份核验，coordinator_kind 用于默认 kind；
team_config 是启动策略，config_path 记录来源。team_id 区分不同调用；record_dir 为
目标项目 `.herdr-recipes/teams/<team_id>/` 的绝对路径。启动器只计算路径，由主协调者
在有准备/工作任务时按 [记录约定](coordinator-records.md) 建立资源表和每轮任务证据。
该目录排除在提交之外，不能当成 GitHub Issues、权威运行状态或自动恢复队列。

每次委托含任务 ID、目标、checkout/基线、写入范围、交付与验证要求。回报格式要求
worker 在正文前单独打印 `DONE <任务 ID>`。派工后的等待必须事件驱动：优先用
`pane wait-output` 等该标记，否则用 `agent wait --until idle` 阻塞；禁止用分钟级
`sleep` 盲等。主协调者核对完成标记、实际文件、来源和检查结果，
idle/done 或 `prompt --wait` 不是任务验收。
开发、review 等 worker 的模型/API 异常与停滞由协调者按
[监督与恢复规则](coordinator-prompt.md#5-监督纠正与验证) 主动检查、恢复并核验进展；
恢复失败达到上限后明确报告阻塞。
多 worktree 由主协调者整合并联合验证，可在完整实现任务中进行必要的本地提交/合并，
保护无关改动；推送、发布依当前任务授权。issue 完成后的主动回收与下一项开始前的
收尾核对，按 [协调规则第 6 节](coordinator-prompt.md#6-整合交付与收尾) 执行；同一任务
后续派工或用户明确要求待命时保留所需 worker，不自动删除 worktree/分支。

启动输出仍为 `ws=... panes=... coordinator=...`，只包含主 pane，兼容现有调用。
正常发一次 Recipe Ready；启动或角色提示词提交失败时发 request 通知、exit 1、保留
workspace。创建前参数错误 exit 2；`--help` 和版本查询不创建资源。未引入 daemon、
后台重试或对话结束后自动接续。决策见 [ADR-0003](../docs/adr/0003-team-scoped-conversational-coordination.md)。

### 验证边界

fixture 测试检查共享配置/上下文、原始任务注入、只启动一个协调者、失败与兼容输出。
它不能证明模型会遵守团队策略。真实模型验收使用隔离 server 与临时项目，保留原生
agent 配置，需要可用的登录和模型环境；不会改写全局配置以绕过权限。
跨 tab/worktree 能力按安装版本的 `--help` 检查；当前 live 场景针对 Herdr 0.9.5，
不据此扩大已有固定 Recipe 的版本兼容声明。

```bash
python3 tests/smoke_hteam_workspace.py --live-agents --binary /path/to/herdr --scenario tabs --output /tmp/tabs.json
python3 tests/smoke_hteam_workspace.py --live-agents --binary /path/to/herdr --scenario worktrees --output /tmp/worktrees.json
python3 tests/smoke_hteam_workspace.py --live-agents --binary /path/to/herdr --scenario failure --output /tmp/failure.json
```

分别检查三 worker 待命后复用/文档汇总与本次预算覆盖、两个独立修改及整合版本、
指定 kind 不可用时的独立工作与部分交付。各场景同时核对主 pane、未交付资源、焦点和
本地记录。默认 pi；其它 kind 需单独实测，脚本入口通过不代表宿主 skill 发现通过。
`tests/smoke_hteam.py --workers 3 --live-agents ...` 保留用户明确要求当前 tab 的几何验收。

## hstatus.sh — 只读 workspace 状态摘要

```bash
hstatus.sh                           # 从 Herdr pane 调用，解析调用方 workspace
hstatus.sh --workspace w3            # 显式 ID，可从 Herdr 外部调用
hstatus.sh --json --workspace w3     # 一行 JSON，诊断仅写 stderr
hstatus.sh --help
hstatus.sh -v                        # 与仓库 VERSION 一致
```

`hstatus.sh` 是 `hstatus.py` 的 Bash 入口，安装后的 scripts 软链会直接包含它，
无需新增键位或重新写配置。依赖 Python 3 和 Herdr 0.8.2+；已在 0.8.2 / 0.9.5
隔离 server 验证。

示例文本：

```text
ws=w3 label="demo" panes=2 order=geometry
  w3:p1 label="实现" kind=codex name=implement status=working
  w3:p2 label="Shell" kind=- name=- status=unknown
```

JSON 契约为 `schema_version: 1`：

| 字段 | 含义 |
|---|---|
| `workspace.workspace_id` / `workspace.label` | 精确 workspace ID；label 可为 null |
| `pane_order` | `geometry` 或 `pane_id` |
| `panes[].pane_id` / `label` | 精确 pane ID；label 可为 null |
| `panes[].kind` / `name` | 已检测的 kind 与 live agent name；缺失为 null |
| `panes[].status` | 当前原始状态，缺失为 `unknown`；未来新状态原样保留 |

默认入口要求 `HERDR_PANE_ID`，通过 `pane current --current` 查询真实调用方，
不会把旧 `HERDR_WORKSPACE_ID` 或用户当前焦点当成调用方。缺少上下文时请使用
`--workspace`。数据来自 workspace / pane 查询和**一次**全量 `agent list`，按精确
pane ID 与 workspace 联接，不会把其它 workspace 的 agent 混进来。

几何通过 `pane layout --pane <选中 workspace 的 pane ID>` 查询，不切焦点。
只有布局覆盖该 workspace 的全部 pane 时才按 `(y,x)` 排序；多 tab、不完整或不可用
几何会在 stderr 告警，所有 pane 统一按完整 `pane_id` 的字典序排序，不做数字化或
大小写折叠。JSON 的 `pane_order` 明确说明实际使用的顺序。

参数错误 exit 2；不存在的 workspace、调用方失效、必需查询的无效 JSON、server
不可用或超时 exit 1，stdout 不输出半份快照。仅可选几何查询失败时降级并 exit 0。
这是顺序查询得到的一次快照，运行中状态可能变化；不推算 elapsed、activity 或
Workflow 完成情况。不会发送 prompt、切换焦点、创建/关闭资源、改配置或安装 hook；
初版不提供 `--watch`。

## hopen.sh — 按代号开 herdr pane 布局

`hopen.sh` 接收一个布局代号，开一个新的 herdr workspace 摆好对应布局，
然后 focus 过去。每一位数字表示从左到右对应列的 pane 数：

| 代号 | 视觉布局       | 用途                    |
|------|---------------|-------------------------|
| 11   | `[A][B]`      | 左右对半               |
| 12   | `[A][B/C]`    | 左大右两窄              |
| 21   | `[A/B][C]`    | 镜像 12                 |
| 13   | `[A][B/C/D]`  | 左大，右列三等分   |
| 31   | `[A/B/C][D]`  | 镜像 13             |
| 22   | `[A/B][C/D]`  | 2×2                     |
| 111  | `[A][B][C]`   | 列宽 50% / 25% / 25% |
| 221  | `[A/B][C/D][E]` | 左四宫格 50% + 右通高 pane 50% |
| 122  | `[A][B/C][D/E]` | 左通高 pane 50% + 右四宫格 50% |

`221` 的列宽为 25% / 25% / 50%，`122` 为 50% / 25% / 25%；四宫格内部
两列等宽、上下等高。终端行列取整允许相差 1 格。

### 用法

命令行：
```bash
bash ~/.config/herdr/scripts/hopen.sh 12
```

或者 source 后当函数用：
```bash
source ~/.config/herdr/scripts/hopen.sh
hopen 12
```

### 配套 keybinding

挂到 `~/.config/herdr/config.toml` 的 `[[keys.command]]` 段：

```toml
[[keys.command]]
key = "prefix+alt+1"
command = "bash ~/.config/herdr/scripts/hopen.sh 12"
description = "hopen 12 — [A][B/C]"

# 2..6 同理绑到 21/22/13/31/111，7 绑 11，8 绑 221，9 绑 122
```

配置改完后热重载：
```bash
herdr config check
herdr server reload-config
```

### 实现要点

- 不硬编码 pane ID，全从 `herdr workspace create` / `herdr pane split`
  返回值取
- 任一步 split 失败，优先关闭新建 ws；失败时逐 pane 兜底，清理未完成会报告 ws ID
- herdr `pane split --direction {right,down}`，没有 horizontal/vertical；
  镜像布局（21/31）靠「先 ROOT right 抢右列，再 ROOT down 抢左下」实现
- 脚本与 keybinding 解耦 —— 想换布局或换键位都只动一处

### 已知限制

- 每次开新 ws，不复用当前 ws；如需"在当前 ws 内调整布局"，另写工具
- 成功创建的 ws 不会由脚本自动关闭；脚本只在 split 失败时回滚，正常关闭由用户负责
- Herdr 0.8.2 为契约下限（catalog 的 `min_herdr_version`），0.9.5 为实测推荐
  （`recommended_herdr_version`）——2026-10-19 在 0.9.5 上复跑全部 9 布局 smoke
  通过。低于推荐版本时 `install.sh` 只警告并说明中文输入法下 prefix 快捷键可能失效；
  0.8.2 与 0.9.5 已分别使用隔离 server 验证两个入口的全部九布局
- `hopen-agents.conf` 不是通用 TOML 解析器；`kind_for` / `prompt_for` 只支持固定
  格式：从第 1 列开始、精确匹配的 `[layout.XX.panes.POS]` section，以及从第 1 列
  开始的单行 `kind = "..."` / `prompt = "..."`。
- 解析器把下一个从第 1 列开始的 `[` header 当作当前 section 的边界；普通字段在
  `kind`/`prompt` 前面不影响匹配，section 的全局排列顺序也不敏感；但嵌套表会提前
  结束搜索范围。
- 多行字符串、TOML 转义、值中的双引号、复杂 TOML 扩展或改变上述格式，可能被当成
  未配置或解析为空；扩展 conf 格式前应先同步修改解析逻辑，不能只改配置文件。

### Agent name 格式

启动后的 agent 在 herdr 里有个 name（`herdr agent list` 能看到），
由 `hopen.sh` 的 `_agent_name` 函数生成。设计原则是「可读
+ 在 herdr 32 字符限制内唯一」。

完整格式：`hopen-<workspace_part>-<position_part>`

- `workspace_part`：从 herdr workspace ID 推出来的低字符版本
- `position_part`：视觉位置名（`left` / `left-top` / `middle-bottom` 等）
- 写入字符仅为 `a-z0-9_-`，超过 32 字符会简写并加 6 位 hash 后缀
- 例：`hopen-w42-left` / `hopen-myproj-main-right-top`
- 长 workspace ID 例：原始拼接超过 32 字符 →
  `hopen-billing-t-0001--a8f3c2`（前缀被截断，后 6 位是 cksum）

为什么需要 hash：herdr agent name 限制 32 字符，达不到唯一性就要 hash。
同一 workspace 里 name 仍可读、跨 workspace 也唯一，herdr 里
`agent list` 可以一眼看出哪个 agent 属于哪个 pane。

如果保留原貌查不到（被截断了），记最后 6 位 hash 就能判别。
静默拿到唯一 name，不需要手工干预。

## hopen-agents.conf

按 layout 代号配置「哪个 pane 启动哪个 agent」。`hopen.sh` 跑完布局后
会读这份 conf，按 section 启动 agent；conf 缺失 / 段缺失 / kind 写错 /
kind 本机没装都不会阻止布局完成，对应 pane 留干净 shell。

### 格式

```toml
[layout.12.panes.left]
kind = "opencode"
pane_name = "实现"           # 可选；不填 = 位置名（left / right-top 等）

[layout.12.panes.right-top]
kind = "pi"

[layout.12.panes.right-bottom]
kind = "hermes"
# prompt = "..."   # 可选；空 = agent 起来后空闲等输入
```

`pane_name` 会交给 `herdr pane rename`，作为 pane 在 herdr 里的显示名。不填或填
空串都回退到位置名本身（left / left-top / right-bottom 等）。

### 位置名（按视觉位置；脚本会把实际创建的 pane 映射到这些名字）

| 代号 | 位置 |
|------|------|
| 11   | left / right |
| 12   | left / right-top / right-bottom |
| 21   | left-top / right / left-bottom |
| 13   | left / right-top / right-mid / right-bottom |
| 31   | left-top / right / left-mid / left-bottom |
| 22   | left-top / right-top / left-bottom / right-bottom |
| 111  | left / middle / right |
| 221  | left-top / middle-top / right / left-bottom / middle-bottom |
| 122  | left / middle-top / right-top / middle-bottom / right-bottom |

21、31、22、221、122 的 pane 创建顺序与视觉顺序不同；脚本会把
`workspace create` 返回的 root pane 和后续 split pane 映射到上面的视觉位置，
配置时只使用位置名，不要按 pane ID 或创建先后猜位置。内部顺序固定为
`idx=0 root`，之后按 `_steps_for` 的 split 顺序追加；例如 21 先创建 `right`，
再创建 `left-bottom`，22 先创建 `right-top`、`right-bottom`，最后才创建
`left-bottom`。

`_steps_for` 的每一步是 `<parent> <direction> [ratio]`。ratio 是**被分裂的 pane
保留**的比例（Herdr 语义）。`13` / `31` 用它等分三 pane 列：一个列里塞 3 个 pane 时，
连续两次对半切会得到 50% / 25% / 25%，所以第一步切走 1/3（`0.333`）、第二步把
剩下的 2/3 对半切（`0.5`）。`221` 先保留左列 1/4，再保留剩余区域的 1/3，
得到 25% / 25% / 50%，保持原有创建顺序与派位。`122` 及其余布局沿用默认比例。

### kind 可选值

`herdr agent start --help` 列出的 kind 共 22 个。本机实测可用（按当前 PATH）：

```
claude codex pi opencode hermes droid qodercli
```

脚本不再用 `command -v` 检查 executable；canonical kind 原样交给 Herdr。
本机未安装、未知或不可用的 kind 会保留 Herdr 的真实诊断，该 pane 留作 shell，
其它 pane 继续，最终 Dispatch 以失败通知汇总。

kind 是 Herdr 的内置 agent 枚举值，不是任意 CLI 名或任意 PATH 可执行文件。
必须使用 `herdr agent start --help` 列出的值；`command -v` 通过只说明本机有同名
命令，不代表 Herdr 接受这个 kind。Herdr 会按 kind 的内置集成启动并检测对应 agent。

### 启动行为

1. 先创建完整布局，再按 conf 中的视觉位置启动 agent。
2. 成功日志形如 `[hopen] ... → hermes on <returned-pane-id>`，写入 stderr；最终的
   `ws=... panes=...` 是 stdout 上唯一的主结果，供脚本调用方读取。
3. agent 启动失败不会回滚布局；失败 pane 保留为干净 shell，其它 pane 继续处理。
   Herdr 返回的真实错误会显示在 stderr，不会被静默吞掉。
4. kind 不在本机 PATH 或 Herdr 不支持时，都由 Herdr 给出诊断后记录为 Dispatch 失败。
5. 配置了 `prompt` 时，agent 启动后直接发送 prompt，不等待 agent 工作完成；prompt
   失败会显示诊断、保留已经启动的 agent，并记录为 Dispatch 失败。
6. 所有 pane 尝试完成后只发一次通知：全部成功为 `Recipe ready` + `done`，任一
   start/prompt 失败为 `Recipe dispatch failed` + `request`。

### 容错策略

| 情况                         | 行为                          |
|------------------------------|-------------------------------|
| conf 文件不存在              | 全部 pane 留干净 shell        |
| `[layout.XX]` 段缺失         | 该 layout 全留干净 shell      |
| `[layout.XX.panes.Y]` 缺失   | 该 pane 留干净 shell          |
| kind 不在 PATH / Herdr 拒绝  | 显示真实错误，pane 留 shell，记录 Dispatch 失败 |
| agent 启动失败               | 显示真实错误，继续其它 pane，记录 Dispatch 失败 |
| prompt 写错 / agent 拒收     | 显示真实错误，agent 保留，记录 Dispatch 失败 |

设计动机：你卸载 agent 或换工具链时，hopen 快捷键不会全废。

## Kind 别名

`_start_agent` 在调用 `herdr agent start --kind` 前会先过一遍 `_resolve_kind`，
把所有别名还原成 herdr 接受的规范名。当前别名表（在 `hopen.sh` 顶部 `_resolve_kind`）：

| 别名 | 解析为 | 备注 |
|---|---|---|
| `op` | `opencode` | |
| `cc` | `claude` | 不是 `claudecode` —— herdr 的 kind 是 `claude` |
| `cd` | `codex` | |

未列出的别名（如 `claude`、`hermes`、`qodercli`）原样传入 herdr。未来 herdr
新增 kind 时不需要改 `_resolve_kind`，直接用全名即可。

可以在两处用别名：

1. `hopen-agents.conf` 的 `kind = "op"`（替代 `kind = "opencode"`）
2. `hopen-once.sh` 的命令行参数（如果有）

别名的目的：缩短配置和命令行的长度。`kind = "op"` 比 `kind = "opencode"`
少 6 个字符，在密集配置的 conf 里能省不少。

## hopen-once.sh — 命令行临时布局

与 chord-driven 的 `hopen.sh` 并列：临时想开一个 layout、每个 pane 起什么
agent 完全命令行指定，bypass `hopen-agents.conf`。

### 用法

```bash
hopen-once.sh K1 K2                # 2 kind → layout 11
hopen-once.sh K1 K2 K3            # 3 kind → layout 12
hopen-once.sh K1 K2 K3 K4         # 4 kind → layout 22
hopen-once.sh --layout CODE K1 K2 K3 K4
hopen-once.sh --layout CODE --kind K1 --prompt P1 --kind K2 --prompt P2 ...
hopen-once.sh --path /tmp --layout 22 -k codex:4
hopen-once.sh -C ../project -l 22 -k codex:4   # 相对路径
hopen-once.sh -C ~ -l 22 -k codex:4            # 家目录
hopen-once.sh --pane-name|-N NAME              # per-pane label（视觉顺序）
hopen-once.sh --help
```

### 指定 workspace 的 cwd（`--path` / `-C`）

`hopen-once.sh` 默认在当前 shell 的 `$PWD` 下开新 workspace。通过 `--path`
（短形式 `-C`）可以指定新 workspace 的根目录：

```bash
hopen-once.sh -C /tmp -l 22 -k codex:4           # 绝对路径
hopen-once.sh -C ../project -l 22 -k codex:4    # 相对路径
hopen-once.sh -C ~ -l 22 -k codex:4             # 家目录
```

路径解析规则：

- 以 `~` 开头：展开为 `$HOME`
- 相对路径：相对当前 shell 的 `$PWD` 解析为绝对路径
- 目录不存在：保留原值传给 `herdr workspace create`，由 herdr 报错
- 默认值：`.`（即当前 `$PWD`），省略 `--path` 时行为不变

`hopen.sh` 本身不暴露 `--path`；如果需要固定 cwd，可在 keybinding 里
把 `--path /some/dir` 硬编码进去。

### 自动 layout

| Kind 数 | 选用 layout |
|---|---|
| 2 | 11（左右对半） |
| 3 | 12（左大 + 右列） |
| 4 | 22（2x2） |
| 其它 | 报错，要求 `--layout` 显式指定 |

`221` 和 `122` 是当前提供的 5-pane layout code；其它 5+ pane 形状仍需在
`hopen.sh` 的 `_steps_for` 里加新 code，并在 `hopen-once.sh` 的 `_h_row_major`
和 `_panes_for` 里补映射。

### Workspace 和 Tab 命名

新开的 workspace 和它的 tab 会自动用 cwd 推导一个名字（多个项目同时开着的时候
一眼能分清）：

- cwd 在 git repo 内 → 当前 git 分支名（`git rev-parse --abbrev-ref HEAD`）
- cwd 不在 git repo 内 → `basename <cwd>`

举例：

```bash
# cwd = ~/projects/foo (foo 是 repo，当前分支是 feature/login)
hopen-once.sh -l 22 -k codex:2 -k pi:2
# → ws label = "feature/login", tab title = "feature/login"

# cwd = /tmp (不是 repo)
hopen-once.sh -l 22 -C /tmp -k codex:4
# → ws label = "tmp", tab title = "tmp"
```

Tab 创建后 herdr 会默认按数字命名（"1", "2", ...）；脚本会调 `herdr tab rename`
把它重命名成同 workspace 一致的名字。

### Pane 命名（`--pane-name` / `-N`）

为每个 pane 设置 label，在 herdr pane tab 上显示。两种配置方式：

**1. conf 驱位（`hopen.sh` + `hopen-agents.conf`）：**

```toml
[layout.22.panes.left-top]
kind = "codex"
pane_name = "实现-A"   # 出现在 herdr 的 left-top pane tab 上

[layout.22.panes.right-bottom]
kind = "pi"
# 不填 → 默认 "right-bottom"
```

**2. 命令行临时（`hopen-once.sh`）：**

`--pane-name`/`-N` 按视觉顺序传给 KINDS[] 同索引位置（跟 `--prompt` 一样）：

```bash
hopen-once.sh -l 22 \
  -k codex -N "codex-A" -p "implement A" \
  -k codex -N "codex-B" -p "implement B" \
  -k pi   -N "review"   -p "review"

hopen-once.sh -l 22 -k codex:2 -k pi:2 -N codex-A -N codex-B -N pi-A -N pi-B
```

**两个简写语法：**

- 逗号分隔：`-N "A,B,C"` 等价于 `-N A -N B -N C`，头尾空白会被 trim。
- 位置参数溢出：位置参数先填 KINDS[]（按视觉顺序），剩下的当 NAMES（prepend 到现有 NAMES 前面）。这样可以一行写完：

```bash
hopen-once.sh -l 21 pi pi codex pi-top pi-right cd-bottom
# KINDS=[pi pi codex], NAMES=[pi-top pi-right cd-bottom]
# → pi-top = 视觉位 1 (left-top)，pi-right = 2 (right)，cd-bottom = 3 (left-bottom)

hopen-once.sh -l 22 -k codex:4 A B C D
# KINDS=[codex×4], NAMES=[A B C D]

hopen-once.sh -l 22 codex codex pi claude A B
# KINDS=[codex codex pi claude], NAMES=[A B]
# → A = 视觉位 1 (left-top)，B = 2 (right-top)
# → 视觉位 3、4 没给名字，回落成 left-bottom / right-bottom
```

`NAMES[i]` 对应第 `i+1` 个**视觉位**，不是 pane ID 顺序。用 `herdr pane list`
核对时注意它按**创建顺序**返回，而 21/31/22 的创建顺序跟视觉顺序不一致，
按行读输出很容易误以为映射错了。上面第三例的实测输出：

```
w4Y:p1  label=A              ← left-top
w4Y:p4  label=left-bottom    ← 兜底（p4 是最后创建的，但在左下）
w4Y:p2  label=B              ← right-top
w4Y:p3  label=right-bottom   ← 兜底
```

**缺省行为：** 不填 / 填空串 → 回退到位置名本身（left / left-top / right-bottom
/ middle 等），所以所有 pane 都会获得一个可读的默认名。

`-N` 是大写 N，因为 `-n` 被 `--no-agents` 占了。

### Kind 位置映射（行优先 / 视觉阅读顺序）

`hopen.sh` 内部按列优先创建 pane。`hopen-once.sh` 把用户传入的 kind 按
**行优先**（视觉阅读顺序）放到 pane：

| layout | 顺序 |
|---|---|
| 11  | left → right |
| 12  | left → right-top → right-bottom |
| 21  | left-top → right → left-bottom |
| 13  | left → right-top → right-mid → right-bottom |
| 31  | left-top → right → left-mid → left-bottom |
| 22  | left-top → right-top → left-bottom → right-bottom |
| 111 | left → middle → right |
| 221 | left-top → middle-top → right → left-bottom → middle-bottom |
| 122 | left → middle-top → right-top → middle-bottom → right-bottom |

例：`hopen-once.sh --layout 22 codex codex pi claude` →
- top-left → codex
- top-right → codex
- bottom-left → pi
- bottom-right → claude

### 与 hopen.sh 的区别

| 维度 | hopen.sh | hopen-once.sh |
|---|---|---|
| 触发方式 | chord (`Cmd+B Alt+N`) 或 `bash hopen.sh 22` | `bash hopen-once.sh K1 K2 ...` |
| Agent 来源 | `hopen-agents.conf` 模板 | 命令行 `--kind` / 位置参数 |
| Layout 来源 | 必须给代码 | 自动选（3/4 kind）或 `--layout` |
| Layout 改后行为 | 每次按 chord 都用同一套 agent | 每次命令独立，不污染 conf |
| 适合场景 | 固定的"打开某 layout"流程 | 临时"我要 N 个 agent 干这活" |
| Workspace cwd | 默认当前 `$PWD` | 默认当前 `$PWD`；可用 `--path/-C` 覆盖 |

两者共用 `_h_build_layout / _position_for / _start_agent / _resolve_kind`，
所以别名 (`op`/`cc`/`cd`) 和 layout 表都互通。

## 与外部 Workflow 组合

根目录 [README 的组合示例](../README.md#compose-with-an-external-workflow) 说明如何把
`hopen.sh` / `hopen-once.sh` 与外部 [herdr-workflows](https://github.com/aorumbayev/herdr-workflows)
组合。本仓库不安装 Workflow 工具，也不新增 wrapper command；`Recipe Ready` 只表示
setup/Dispatch 成功（通知标题为 `Recipe ready`），不表示 agent task 已完成。
