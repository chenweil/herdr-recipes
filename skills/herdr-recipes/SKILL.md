---
name: herdr-recipes
description: "用 Herdr Recipes 准备固定 pane 布局、新建 hteam 团队、由当前 agent 协调 worker，或通过 hstatus 查看状态。用户明确要求用 Herdr 开 agent、先待命后委托或汇总团队结果时使用；通用 Herdr CLI 操作交给全局 herdr skill。"
---

# Herdr Recipes

把用户意图交给现有入口；脚本负责 Recipe 的准备与 Dispatch，对话中的协调者负责派工和核验。

## 定位与选择

先解析本 SKILL.md 的真实路径（包括软链），取其所在目录的上两级为项目根目录；以下
链接均相对真实 skill 目录。工作目录取用户的目标项目，缺省为当前任务目录，保留它与脚本目录的区别。
执行所选脚本前读取它的 `--help`；参数、kind alias 和布局映射以脚本为准。

按用户要求选择一条路径：

| 用户意图 | 入口与完成条件 |
|---|---|
| 新工作区中开指定布局、固定数量/kind/初始提示词 | 使用 `scripts/hopen-once.sh`；已有 conf 配方使用 `scripts/hopen.sh`。逐项报告布局、启动和初始提示词提交结果。 |
| 新建一个可持续对话的团队 | 使用 `scripts/hteam.sh`，将初始要求原样放入 `-p`，目标目录传入 `-C`；`-k` 只选主协调者。成功后交付返回的工作区/主 pane，由新主 agent 接续；worker 准备结果需后续核对。 |
| 当前 agent 准备 worker、后续派工或汇总 | 进入下方“当前 tab 协调”，保留当前主对话。 |
| 仅查看 pane/agent 状态 | 使用 `scripts/hstatus.sh`，当前调用方或用户指定的 workspace，按需 `--json`。状态只说明现场，不证明任务完成。 |

只解释用法时给出命令或提示示例。为准备团队而调用脚本时，使用完整参数执行已安装入口，
无需读取整个脚本实现。启动失败时保留诊断和返回的 ID，先核对部分成功状态再决定下一步。

## 当前 tab 协调

1. 读取全局 **herdr** skill，并通过其 `HERDR_ENV=1` 检查。依赖不可用时说明缺失并停在
   操作前；位于 Herdr 外时，只有明确的新团队要求才走 `hteam.sh`，其它情况先确定目标。
2. 用 `herdr pane current --current` 的实际响应确定 workspace、tab 和主 pane，再查同
   workspace 的 pane 与 agent。完成条件是精确作用域已核对，已有团队成员与其它 agent 已区分。
3. 首次担任协调者时读取 [协调规则](../../scripts/coordinator-prompt.md)，用步骤 2 的
   实际 ID 填入 runtime context；cwd 为目标项目，launcher_core/status_entry 分别为项目根下的
   `scripts/hopen.sh` / `scripts/hstatus.sh`。后续对话沿用该规则，上下文变化时重新核对。
4. 执行本次准备、委托或汇总。只准备时给角色和待命要求；有任务时由协调者根据目标决定数量
   和分工，遵守用户给定的 kind、数量与上限。逐 worker 核对实际响应，并按协调规则读取实际
   pane 几何后汇报布局和准备/派发/验证结果。

全局 herdr skill 提供 CLI 操作知识；协调规则提供团队作用域、布局、任务和证据约定。
两者按当前分支读取，共用协调规则已在上下文时直接复用。
