# herdr scripts

`config.toml` 里 `[[keys.command]]` 调到的本地脚本都放这里。

## version.sh — 版本号读取

版本号存在仓库根的 `VERSION` 文件，`version.sh` 是唯一读取入口，
被 `hopen.sh` / `hopen-once.sh` / `herdr-pane-switch.sh` / `install.sh` source；
`herdr-pane-switch.py` 自己读同一个文件。发版只改 `VERSION` 一处。

```bash
hopen.sh -v                 # hopen 0.4.0
hopen-once.sh --version     # hopen-once 0.4.0
herdr-pane-switch.sh -V     # herdr-pane-switch 0.4.0
```

安装后 `~/.config/herdr/scripts` 是指向仓库的 symlink，所以 `_hr_version` 用
`pwd -P` 解析真实路径再拼 `../VERSION`；用逻辑路径会落到
`~/.config/herdr/VERSION`（不存在）。读不到时输出 `unknown` 而不报错。

每个版本改了什么看仓库根的 [CHANGELOG.md](../CHANGELOG.md)。

## hopen.sh — 按代号开 herdr pane 布局

`hopen.sh` 接收一个布局代号，开一个新的 herdr workspace 摆好对应布局，
然后 focus 过去。代号约定「左列数 + 右列数」：

| 代号 | 视觉布局       | 用途                    |
|------|---------------|-------------------------|
| 11   | `[A][B]`      | 左右对半               |
| 12   | `[A][B/C]`    | 左大右两窄              |
| 21   | `[A/B][C]`    | 镜像 12                 |
| 13   | `[A][B/C/D]`  | 左大右三窄              |
| 31   | `[A/B/C][D]`  | 镜像 13                 |
| 22   | `[A/B][C/D]`  | 2×2                     |
| 111  | `[A][B][C]`   | 三列等宽                |

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

# 2..6 同理绑到 21/22/13/31/111，7 绑 11
```

配置改完后热重载：
```bash
herdr config check
herdr server reload-config
```

### 实现要点

- 不硬编码 pane ID，全从 `herdr workspace create` / `herdr pane split`
  返回值取
- 任何一步 split 失败，回滚已开的 pane 并关掉整个 ws
- herdr `pane split --direction {right,down}`，没有 horizontal/vertical；
  镜像布局（21/31）靠「先 ROOT right 抢右列，再 ROOT down 抢左下」实现
- 脚本与 keybinding 解耦 —— 想换布局或换键位都只动一处

### 已知限制

- 每次开新 ws，不复用当前 ws；如需"在当前 ws 内调整布局"，另写工具
- 成功创建的 ws 不会由脚本自动关闭；脚本只在 split 失败时回滚，正常关闭由用户负责
- herdr 0.8.0 实测通过；旧版 API 差异自担
- `hopen-agents.conf` 不是通用 TOML 解析器；`kind_for` / `prompt_for` 只支持固定
  格式：从第 1 列开始、精确匹配的 `[layout.XX.panes.POS]` section，以及从第 1 列
  开始的单行 `kind = "..."` / `prompt = "..."`。
- 解析器把下一个从第 1 列开始的 `[` header 当作当前 section 的边界；普通字段在
  `kind`/`prompt` 前面不影响匹配，section 的全局排列顺序也不敏感；但嵌套表会提前
  结束搜索范围。
- 多行字符串、TOML 转义、值中的双引号、复杂 TOML 扩展或改变上述格式，可能被当成
  未配置或解析为空；扩展 conf 格式前应先同步修改解析逻辑，不能只改配置文件。

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
| 21   | left-top / left-bottom / right |
| 13   | left / right-top / right-mid / right-bottom |
| 31   | left-top / left-mid / left-bottom / right |
| 22   | left-top / left-bottom / right-top / right-bottom |
| 111  | left / middle / right |

21、31、22 的 pane 创建顺序与视觉顺序不同；脚本会把
`workspace create` 返回的 root pane 和后续 split pane 映射到上面的视觉位置，
配置时只使用位置名，不要按 pane ID 或创建先后猜位置。内部顺序固定为
`idx=0 root`，之后按 `_steps_for` 的 split 顺序追加；例如 21 先创建 `right`，
再创建 `left-bottom`，22 先创建 `right-top`、`right-bottom`，最后才创建
`left-bottom`。

### kind 可选值

`herdr agent start --help` 列出的 kind 共 22 个。本机实测可用（按当前 PATH）：

```
claude codex pi opencode hermes droid qodercli
```

其它 kind 在本机没装时，`hopen.sh` 会提示不在 PATH 并跳过该 pane，其它正常。

kind 是 Herdr 的内置 agent 枚举值，不是任意 CLI 名或任意 PATH 可执行文件。
必须使用 `herdr agent start --help` 列出的值；`command -v` 通过只说明本机有同名
命令，不代表 Herdr 接受这个 kind。Herdr 会按 kind 的内置集成启动并检测对应 agent。

### 启动行为

1. 先创建完整布局，再按 conf 中的视觉位置启动 agent。
2. 成功日志形如 `[hopen] ... → hermes on <returned-pane-id>`，写入 stderr；最终的
   `ws=... panes=...` 是 stdout 上唯一的主结果，供脚本调用方读取。
3. agent 启动失败不会回滚布局；失败 pane 保留为干净 shell，其它 pane 继续处理。
   Herdr 返回的真实错误会显示在 stderr，不会被静默吞掉。
4. kind 命令不在 PATH 时直接跳过；命令虽然存在但 Herdr 不支持时，会显示 Herdr
   的拒绝原因后跳过。
5. 配置了 `prompt` 时，agent 启动后再发送 prompt；prompt 失败不影响已经启动的
   agent。

### 容错策略

| 情况                         | 行为                          |
|------------------------------|-------------------------------|
| conf 文件不存在              | 全部 pane 留干净 shell        |
| `[layout.XX]` 段缺失         | 该 layout 全留干净 shell      |
| `[layout.XX.panes.Y]` 缺失   | 该 pane 留干净 shell          |
| kind 命令不在 PATH           | 提示并跳过该 pane             |
| Herdr 拒绝 kind / 启动失败   | 显示真实错误并跳过该 pane     |
| prompt 写错 / agent 拒收     | 跳过 prompt，agent 仍启动     |

设计动机：你卸载 agent 或换工具链时，hopen 快捷键不会全废。

## Kind 别名

`_start_agent` 在调用 `herdr agent start --kind` 前会先过一遍 `_resolve_kind`，
把所有别名还原成 herdr 接受的规范名。当前别名表（在 `hopen.sh` 顶部 `_resolve_kind`）：

| 别名 | 解析为 | 备注 |
|---|---|---|
| `op` | `opencode` | |
| `cc` | `claude` | 不是 `claudecode` —— herdr 的 kind 是 `claude` |
| `cd` | `codex` | |
| `pi` | `pi` | 等价于原名；保留是为了对称 |

未列出的别名（如 `claude`、`hermes`、`qodercli`）原样传入 herdr。未来 herdr
新增 kind 时不需要改 `_resolve_kind`，直接用全名即可。

可以在三处用别名：

1. `hopen-agents.conf` 的 `kind = "op"`（替代 `kind = "opencode"`）
2. `hopen-once.sh` 的命令行参数（如果有）
3. 直接调用 `hopen.sh` 时的 `--kind` / `--kind` / ...

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

当前 repo 没有 5+ pane 的 layout code；如需更多 pane，先在 `hopen.sh` 的
`_steps_for` 里加新 code + 在 `hopen-once.sh` 的 `_h_row_major` / `_panes_for`
里补映射。

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
hopen-once.sh -l 21 pi pi codex pi-top pi-bot cd-right
# KINDS=[pi pi codex], NAMES=[pi-top pi-bot cd-right]
# → pi-top = 视觉位 1 (left-top)，pi-bot = 2 (left-bottom)，cd-right = 3 (right)

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
| 21  | left-top → left-bottom → right |
| 13  | left → right-top → right-mid → right-bottom |
| 31  | left-top → left-mid → left-bottom → right |
| 22  | left-top → right-top → left-bottom → right-bottom |
| 111 | left → middle → right |

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
所以别名 (`op`/`cc`/`cd`/`pi`) 和 layout 表都互通。

