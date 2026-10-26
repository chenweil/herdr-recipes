---
name: herdr-recipes
description: "用户要求用 Herdr 准备固定布局、新建团队、当前对话协调跨 tab/worktree 的 agent 工作、先待命后委托、汇总结果或 issue 完成收尾与切换时使用；只读状态走 hstatus，通用 CLI 知识交给全局 herdr skill。"
---

# Herdr Recipes

把用户意图交给现有入口；脚本负责 Recipe 的准备与 Dispatch，对话中的协调者负责派工和核验。

## 定位与选择

先解析本 SKILL.md 的真实路径（包括软链），取其所在目录的上两级为项目根目录；以下
链接均相对真实 skill 目录。工作目录取用户的目标项目，缺省为当前任务目录，保留它与脚本目录的区别。
执行所选脚本前读取它的 `--help`；参数、kind alias 和布局映射以脚本为准。

先识别固定 Recipe：用户给出布局代号并指定各 pane 的 kind 时，优先读取
[固定配方请求](../../scripts/recipe-request.md)并执行，完成后结束该分支。其它请求按下表选择：

| 用户意图 | 入口与完成条件 |
|---|---|
| 指定布局/kind/初始提示词，或已有 conf 配方 | 使用 `scripts/hopen-once.sh`；已有 conf 配方使用 `scripts/hopen.sh`。逐项报告布局、启动和初始提示词提交结果。 |
| 新建一个可持续对话的团队 | 使用 `scripts/hteam.sh`，将初始要求原样放入 `-p`，目标目录传入 `-C`；`-k` 只选主协调者。成功后交付返回的工作区/主 pane，由新主 agent 接续；worker 准备结果需后续核对。 |
| 当前 agent 准备 worker、处理完整任务、后续派工、汇总或 issue 收尾与切换 | 进入下方“当前对话协调”，保留当前主对话；未给布局的“三个 agent 调研并汇总”也属于此路径。 |
| 仅查看 pane/agent 状态 | 使用 `scripts/hstatus.sh`，当前调用方或用户指定的 workspace，按需 `--json`。状态只说明现场，不证明任务完成。 |

只解释用法时给出命令或提示示例。为准备团队而调用脚本时，使用完整参数执行已安装入口，
无需读取整个脚本实现。启动失败时保留诊断和返回的 ID，先核对部分成功状态再决定下一步。

## 当前对话协调

1. 读取全局 **herdr** skill，并通过其 `HERDR_ENV=1` 检查。依赖不可用时说明缺失并停在
   操作前；位于 Herdr 外时，只有明确的新团队要求才走 `hteam.sh`，其它情况先确定目标。
2. 已有启动器 runtime context 时，用其中精确的 coordinator_pane_id 查询 `pane current --pane`；
   原地接管且没有该上下文时才用 `pane current --current`。核对响应中的 workspace/tab/pane，
   再查同 workspace 的 tab、pane 与 agent，识别当前主 agent 的实际 kind 和 live name。
   这些只是初始位置；团队只接管本次创建或用户明确交付的资源。
3. 首次担任协调者时读取 [协调规则](../../scripts/coordinator-prompt.md)。已有启动器
   context 就沿用；否则读取 `scripts/hteam-context.sh --help`，传步骤 2 的真实
   `--workspace/--tab/--pane/--kind`、目标 `-C`，有 live name 才传 `--name`，取得相同
   格式的 runtime context。该入口不调用 Herdr、不创建资源；同 hteam 共用配置解析。
   默认读目标目录 `.herdr-team.json`，或用户指定 `--config` / `HTEAM_CONFIG`；可用
   `--worker-kind` / `--max-workers` 覆盖启动配置。配置无效先报告，不静默忽略。
4. 将用户任务原样交给同一套协调规则，解释本次有效约束后推进。只准备时仅给角色和
   待命要求；完整任务则持续派工、核对交付和整合验证，到完成或真实阻塞再交付。
   worker 默认沿用主 kind，总预算四个，用户本次明确指令可临时覆盖。跨 tab/worktree
   的归属、轻量记录、独立写入、失败与回收由协调规则统一规定。任务技能提供专业方法，
   agent 委托全部经 Herdr，不使用宿主 sub-agent 替代。汇报布局前逐 tab 核对真实矩形。

全局 herdr skill 提供 CLI 操作知识；协调规则提供团队归属、资源策略、任务和证据约定。
协调规则（coordinator-prompt.md）列有四个必守约束：① 管理/调度角色（协调者与
review 角色）不在主对话现场直接运行测试，改在独立 tab/pane 中执行并回现场核对；
② 每个 issue 的开发独占一个以 issue 标识命名的新 tab，不在既有 issue 的 tab 上做新 issue。
③ issue 完成后按协调规则第 6 节主动回收该 issue 的临时 tab/pane 和 worker，开始下一项
前补查收尾；核验与存档后直接执行，无需等用户提醒或再次确认，保留项须说明原因。
④ 所有受委派角色（含开发与 review）由协调者持续监督；模型/API 报错或停滞时按第 5 节
主动恢复原会话，必要时重建同角色 worker，并核验新进展；达到恢复上限则报告阻塞。
⑤ 派工后等待必须事件驱动：优先用 `pane wait-output` 等约定的 `DONE <任务 ID>` 标记，
否则用 `agent wait --until idle` 阻塞；禁止用分钟级 `sleep` 盲等，单次观察型 sleep
也不超过 60 秒。标记与超时细节见第 5 节「派工后的等待方式」。
⑥ `team_config.skills` 已配置的角色强制用指定技能：实现、审查、排查不得用其它方法
替代，技能不可用则报告阻塞；未配置的角色按建议处理，提议并写进提示词。不确定
下一步做什么时先走 `route` 技能定位阶段，不凭印象猜。见第 4 节「角色技能约定」。
六者按当前分支读取，共用协调规则已在上下文时直接复用。
