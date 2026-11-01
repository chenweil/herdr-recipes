# flow 文件规范

`hsuper` 读一个声明式流程文件，然后按它执行并监督。流程文件是纯声明：
它描述**做什么**，不描述**怎么等**、**怎么恢复**——后者是监督器的固定策略。

- 位置：仓库根 `flows/<name>.toml`（可用 `--flow-file PATH` 指定任意路径）。
- 一个文件一个流程：`[flow.<name>]` 段有且仅有一个。
- 格式：TOML，由 Python 3.11+ 的 `tomllib` 解析（无第三方依赖）。

```bash
hsuper.sh --flow dev-review --task "实现登录功能"
hsuper.sh --flow-file ./my-flow.toml --task "..." --dry-run
```

## 字段

### 流程级（`[flow.<name>]`）

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `description` | string | `""` | 人读描述 |
| `max_parallel` | int | `4` | 同时存活的 worker 上限；独立步骤由线程并发执行，名额满时等待；`0` = 不限，`1` = 严格串行 |
| `cwd` | string | `-C` 或 `$PWD` | 团队工作目录 |
| `layout` | string | 按步骤数自动 | 复用 hopen 布局代号（`11` / `12` / `22` / `31` / `221` …） |
| `start` | string[] | 所有入度为 0 的步骤 | 显式指定起始步骤 |
| `defaults` | table | 见下 | 步骤默认值 |

### 默认值（`[flow.<name>.defaults]`）

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `kind` | string | `codex` | agent kind；支持 `op` / `cc` / `cd` 别名 |
| `model` | string | `""` | 空 = 沿用该 agent 自身配置；非空 = 展开为 `--model <m>` |
| `args` | string[] | `[]` | 原样透传给 agent 的原生参数（排在 `--model` 之后） |

### 步骤（`[flow.<name>.steps.<id>]`）

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `role` | string | `<id>` | pane 标签 / 报告里的角色名 |
| `kind` | string | `defaults.kind` | 覆盖默认 kind |
| `model` | string | `defaults.model` | 覆盖默认模型 |
| `args` | string[] | `defaults.args` | 覆盖（**非追加**）默认参数 |
| `prompt` | string | — | 步骤提示词模板 |
| `prompt_file` | string | — | 提示词文件路径（相对仓库根）；与 `prompt` 二选一 |
| `next` | string[] | `[]` | 本步骤完成后派发的步骤（**后继**） |
| `expect` | string[] | `[]` | 交付证据路径 / glob；`gate` 的判据 |
| `gate` | `"artifact"` \| `"none"` | `expect` 非空时为 `artifact` | 推进前是否必须看到证据 |
| `timeout_ms` | int | `900000` | 单步单次等待预算（毫秒） |
| `retries` | int | `2` | 原会话恢复次数上限 |
| `rebuilds` | int | `1` | 重建次数上限 |
| `cwd` | string | 流程的 `cwd` | 该步骤的 pane 工作目录 |

## `next` 的方向

`next` 指向的是**后继**：`a.next = ["b"]` 表示 a 完成后才派发 b。

因此：

- **起始步骤**（root）是**没有人指向**它的步骤，不是 `next` 为空的步骤。
  没有 `next` 的步骤是**叶子**。
- 一个步骤的**前置**是那些在自己的 `next` 里写了它的步骤。
- `start` 可以显式指定起始步骤；不写时取所有入度为 0 的步骤。

## 占位符

只有这五个名字合法，其它一律报错（防止手写近义名后静默留空）：

| 占位符 | 展开为 |
|---|---|
| `{{task}}` | `--task` 的内容 |
| `{{run_id}}` | 本次 run 的短 ID |
| `{{step}}` | 当前步骤 ID |
| `{{marker}}` | 完成标记，形如 `DONE r7f3-dev` |
| `{{run_dir}}` | 本次 run 的目录（`.herdr-recipes/runs/<时间戳>-<run_id>/`） |

`prompt`、`prompt_file` 与 `expect` 都走同一套展开。`{{task }}`、`{{TASK}}`
这类近义写法会**报错**，不会展开成空串。

## 校验规则

`--dry-run` 会把下面八条全部检查一遍，任何一条不过就以退出码 1 失败：

1. `next` 指向的步骤必须存在。
2. 依赖图必须无环（`flow: cycle detected: a -> b -> a`）。
3. 每个步骤必须能从 `start` 到达，否则 `unreachable step`。
4. `prompt` 与 `prompt_file` 必须有且仅有一个。
5. `kind` 必须落在本机 `herdr agent start --help` 的 possible values 内
   （运行时校验，不在解析期硬编码列表；`op` / `cc` / `cd` 别名先解析再校验）。
6. `retries` 不得超过 `2`、`rebuilds` 不得超过 `1`。超过要用
   `--allow-unsafe-limits` 显式放宽——这 2+1 是**策略**，不是能力，
   悄悄改成无限重试会让整个提案失去意义。
7. 步骤 ID 必须匹配 `[a-z][a-z0-9_-]{0,31}`（同时是 herdr 的 agent 名字规则）。
8. `{{...}}` 占位符只允许上表五个名字。

## 证据门槛（`gate`）

`expect` 非空时 `gate = "artifact"`。一个步骤只有在下面两条同时成立时才推进：

1. `expect` 里的每个路径都存在（相对步骤的 `cwd`，或绝对路径；含通配符时
   按 glob 匹配）；
2. `result/<step>.json` 存在、能解析，且其中声明的 `status` 是 `done`。

**标记本身只是候选。** `pane wait-output` 会立刻搜索已有快照，所以只要
标记串出现在屏幕上（包括被回显），它就会命中。门槛就是用来把这种回显挡在
外面的。

## 完成标记与交接

- worker 必须在终端**单独一行**打印 `{{marker}}`，并把结构化结果写进
  `result/<step>.json`。
- 监督器提交给 agent 的**只有**一句「读取 `<run_dir>/prompts/<step>.md`
  并执行」。标记串因此不会出现在任何命令行里——这正是 §9.3 的回显陷阱
  要防的事。
- agent 之间传数据走文件，不走 `agent read` + 翻滚屏。

## 恢复预算

`retries` / `rebuilds` 沿用协调规则 §5 的数字：

| 序 | 触发 | 动作 |
|---|---|---|
| 1–2 | worker 报错 / 超时停滞 / 状态不可判定 | **原会话恢复**：向同一会话提交继续提示 |
| 3 | 原会话恢复失败，或 worker 已消失 | **重建**：同 pane 同名重启 + 交接记录 |
| 4+ | 重建后仍同类故障 | **分支失败**：记 `state.json` + 通知人，其它分支继续 |

- `gone`（worker 身份消失）直接进重建——原会话已经不存在，没法恢复。
- 重建会先停掉旧会话再等名字释放：herdr 会**短暂保留** agent 名字，
  抢着重建会撞上 `agent_name_taken`（本机实测）。
- 旧 pane 没能回到 shell 提示符时，重建回退到新 pane，并写进
  `state.json` 的 `deviations[]`——不允许静默偏离。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 全部步骤通过 |
| 1 | 启动 / 参数 / flow 校验错误 |
| 20 | 至少一个分支失败（恢复预算用尽） |
| 21 | 至少一个分支 `blocked`（等人工输入） |
| 22 | 全局超时预算耗尽 |
| 23 | 部分成功：没有分支失败或阻塞，但有步骤被跳过 |

## 状态与续跑

每次状态变更都原子写（写 `.tmp` 再 `os.replace`），所以监督器被杀也留下
一份可读的状态。

`--resume` 重跑 `status` 为 `pending` / `running` / `failed` 的步骤；
`done` 的步骤跳过且不重新派工；`blocked` 的步骤保持原状。流程定义从
run 目录里存的 `flow.toml` 读，所以改了 `flows/*.toml` 也不会影响已经
开始的那次 run。
