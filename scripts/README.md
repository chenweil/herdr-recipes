# herdr scripts

`config.toml` 里 `[[keys.command]]` 调到的本地脚本都放这里。

## hopen.sh — 按代号开 herdr pane 布局

`hopen.sh` 接收一个布局代号，开一个新的 herdr workspace 摆好对应布局，
然后 focus 过去。代号约定「左列数 + 右列数」：

| 代号 | 视觉布局       | 用途                    |
|------|---------------|-------------------------|
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

# 2..6 同理绑到 21/22/13/31/111
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

[layout.12.panes.right-top]
kind = "pi"

[layout.12.panes.right-bottom]
kind = "hermes"
# prompt = "..."   # 可选；空 = agent 起来后空闲等输入
```

### 位置名（按视觉位置；脚本会把实际创建的 pane 映射到这些名字）

| 代号 | 位置 |
|------|------|
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
