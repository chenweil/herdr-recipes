# Herdr 上游更新追踪（2026-09 ~ 2026-10）

统计时间：2026-10-19（初次统计 2026-10-10，已按 0.9.4 / 0.9.5 更新）
本机版本：`herdr 0.9.5`（stable 通道，已装 2026-10-16）
上游仓库：`github.com/herdrdev/herdr`

## 一、版本时间线

| 版本 | 发布日期 | 性质 |
|---|---|---|
| v0.9.0 | 2026-09-07 | 大版本（多机器、多客户端、图形默认开启） |
| v0.9.1 | 2026-09-16 | 功能版（`herdr --machine`、Windows SSH、输入框行编辑、Letta） |
| v0.9.2 | 2026-09-29 | 功能版 + **1 个 Breaking Change** |
| v0.9.3 | 2026-09-29 | 热修复（Escape 键序列回归） |
| v0.9.4 | 2026-10-13 | 大版本（输入管线重写、会话持久化、性能） |
| v0.9.5 | 2026-10-14 | 0.9.4 的 hotfix |

**结论：0.9.5 是当前 stable 最新，本机已装。** 9 月一个月发了 4 个版本，
10 月又发了 2 个，节奏比 9 月还快——上游没有减速的迹象。


## 二、值得关注的功能（按我判断的实用度排序）

### 1. Agent 自报 resume 命令（0.9.2，#4687）— 最重要
Agent 可以自己告诉 Herdr「怎么恢复我的会话」。之后 **server 重启能原样恢复 agent 会话，不再依赖 Herdr 内置集成**。
配套出了 [Add Herdr support to your agent](https://herdr.dev/docs/add-herdr-support/) 指南（state / resume / release 三个协议）。
→ 意义：以后我们自己写的 agent、或者上游还没支持的 agent，只要实现自报就能被 Herdr 正确恢复。这是「集成不再是瓶颈」的信号。

### 2. `herdr --machine <label>` CLI 转发（0.9.1，#3918）
不开 Herdr 窗口也能在远端机器上操作 agent / pane / workspace / worktree。
失败不回退到 Local（显式错误）。
→ 配合 0.9.0 的「本地 + 已保存 SSH 机器统一在一个窗口管理」（#3670），Herdr 现在是真·多机 agent 驾驶舱：一个侧栏看全部机器的 agent 状态，键盘导航跨机器切换。

### 3. 机器连接体验大修（0.9.2）
- `herdr machine status` 非交互检查；`herdr machine reconnect` 在你自己的终端里完成 SSH 认证（含 MFA）
- 打开的客户端每 30 秒重查失败机器，修好连接不用重启
- 交互式 `machine add` 会自动发现主机上已在跑的 Herdr 会话让你选；`--label` 可选，默认用 SSH 主机名命名
- SSH 请求压缩（#4340）；远端滚动输出只发变化的行（#4711）→ 慢链路上 build 日志和流式 agent 输出省一大截带宽

### 4. 多 prefix 键（0.9.2，#4653）
```toml
[keys]
prefix = ["ctrl+space", "ctrl+s"]
```
每个键都能进 prefix 模式，`prefix+?` 会把它们全列出来。

### 5. Go To 重构（0.9.2，#4384）
每个 agent / terminal 独立成一行，按 workspace 分组，带状态和路径；左右键在 workspace 之间跳。

### 6. `keys.clear_pane`（0.9.2，#4383）
清当前 pane 的屏幕和 scrollback，但**保留当前 prompt 行**。默认不绑定，且会放过 Vim 这类全屏程序。

### 7. 恢复 agent 逐个启动（0.9.2，#4102）
默认 100ms 间隔，不再一口气全起来。可调：
```toml
[session]
startup_per_agent_delay_ms = 100
```

### 8. 性能：多 pane 场景 CPU 大幅下降（0.9.2）
navigator、pane 分裂、named target 在大 session 里都快了；外部事件突发不再卡渲染（#4506 #4546 #4426 #4669 #4670 #4671 #4672）。
另有 0.9.0 的「idle scrollback 内存下降」（#3556）、「UI 移到客户端进程」（#3487）。

### 9. 布局快照保命（0.9.2，#4320）
主机关机 / 恢复失败时布局快照不再丢。Linux logind 下会在关机前主动保存；所有平台在 `session-snapshots/` 保留**最多 48 份**供手工恢复。
→ 你机器上 `~/.config/herdr/session-snapshots/` 已经在用了（10-10 03:13 有写入）。

### 10. 输入框终于能编辑了（0.9.1，#1803 #3698）
改名、过滤、搜索框支持字符/词移动、Home/End、删除，以及 Ctrl+A/E/K/U/W/Y，Unicode 也行。

### 11. 链接按住 Ctrl 高亮（0.9.1，#1282）
折行 URL 和 OSC 8 链接，即使首尾在视口外也可点。

### 12. 侧栏 token 规则增强
- 0.9.0：文本/数值规则按值改颜色、加粗、变暗（#3693）
- 0.9.1：`hide = true` 直接隐藏匹配的 token 及分隔符（#3925）

### 13. 新 agent 检测
- **Muse**（0.9.0，#2489）：idle / working / approval / question 状态
- **Letta Code**（0.9.1，#3106 #3107）：含原生会话恢复，集成走 `herdr integration install letta`（不在 Settings 列表里）

### 14. 一堆 agent 状态检测修复
0.9.0：Claude Code 后台任务/MCP 问题保持 blocked（#3283 #3383 #2650 #3615）、Codex 不再被历史确认文本误判 blocked（#3301）、Copilot 后台 agent 期间保持 working、OpenCode 子 agent 不再卡父 pane（#3669）。
0.9.1：OpenCode V2 从显示会话的 pane 报状态（#3757）、Codex 静态标题/禁用动画时仍判 working（#4092 #4099 #3988）。
0.9.2：OpenCode V1 子 agent 期间保持 working、Codex 不再在有输出时报 idle、Kiro 审批提示报 blocked、Droid 清屏被尊重。

## 三、需要注意的破坏性变更 / 行为变化

| 变更 | 版本 | 影响 |
|---|---|---|
| `pane.graphics.info/set/clear/stream` 移除，返回 `unknown_method` | 0.9.2 | 走标准 Kitty 图形协议；Herdr 原生渲染。用这个 API 的脚本要改 |
| 单进程 `--no-session` 模式移除 | 0.9.0 | 所有启动都 attach 到后台 server |
| 关主 workspace 且还有 worktree workspace 时，必须显式 `--group` / `close_group: true` | 0.9.0 | 否则整组不关 |
| 新生命周期事件订阅**不再重放历史** | 0.9.0 | API 客户端必须先订阅、再拉初始快照，否则漏事件 |
| 事件订阅严重落后时返回 `events_lost` 错误而非静默丢事件 | 0.9.2 | 消费者要处理这个错误 |
| 新 pane 设 `TERM_PROGRAM=herdr`，不再继承宿主终端 session ID | 0.9.2 | 依赖 iTerm2/Claude session marker 的脚本要适配 |
| `pane split` 不带目标时，分裂**调用者**的 pane | 0.9.1 | 在 Herdr 内部调 CLI 时行为变了 |
| 关最后一个 tab 需要确认 | 0.9.2 | 脚本化清理会多一步 |

## 四、对本仓库（herdr-recipes）的影响

已核对，当前 0.7.0 与 0.9.5 兼容性没问题（2026-10-19 在 0.9.5 上实测），但有 4 个点值得记一笔：

1. **`prefix` 支持数组不影响 install.sh**。install.sh 用 `^\s*prefix\s*=` 检测已有前缀，`prefix = ["ctrl+space","ctrl+s"]` 同样匹配，不会被覆盖（已实测）。但 legacy 清理正则只管 `prefix+1..6` / `prefix+(alt|ctrl)+1..9`，数组形式下的自定义键位仍归你自己管。
2. **`pane split` 语义变化（0.9.1）不影响本仓**。已在 0.9.5 上复跑 9 布局 smoke matrix 验证：`hopen.sh` 的 `_h_split` 永远显式传 target，9 个布局的 pane 数与 `(y,x)` 排序后的自然顺序与 0.9.1 记录逐条一致。详见 `docs/herdr-research.md` 的「九布局 smoke matrix（Herdr 0.9.5）」节，issue #10 已关闭。
3. **`startup_per_agent_delay_ms`（0.9.2）与 hopen 的批量 dispatch 无关**。这个间隔只作用于**恢复**的 agent，而 hopen 开的是**新建** pane，不走恢复路径，默认 100ms 不会造成 dispatch 抢跑。
4. **agent catalog 仍是 0.9.1 基线**。`herdr integration list` 当前仍是 18 个 target（pi/omp/claude/codex/copilot/devin/droid/kimi/opencode/kilo/hermes/qodercli/qwen/letta/cursor/mastracode/antigravity-cli/grok），与 `config/agent-catalog.json` catalog v2 一致，**0.9.2 ~ 0.9.5 没有新增 integration target**，不用 bump。

## 五、0.9.4 / 0.9.5 里与本仓库相关的变化（2026-10-19 核对）

- **`herdr plugin update`（0.9.4，#4245）** — 从 GitHub 安装的 plugin 可批量更新，
  本地软链的 plugin 不动。与本仓无直接关系，但同属 herdr 配置面。
- **输入管线重写 `libghostty`（0.9.4，#5017）** — 移除了实验性的
  `HERDR_WINDOWS_INPUT_BACKEND=crossterm`；Ctrl+Shift+letter 到普通 shell 现在按
  Ctrl 处理，Super / Hyper / Meta 不再往 pane 里灌转义码垃圾。
- **非拉丁键盘布局的 Ctrl 快捷键修复（0.9.4，#1079 #4961）** — 俄语 / 希腊 / 希伯来等
  布局下 Ctrl+W 等恢复正常，AltGr 字符语义不变。
- **中文输入法下 prefix 快捷键修复（0.9.5，#5255）** — 中文 / 日文 / 韩文输入法把
  下一个键作为纯文本发送时，prefix 快捷键又好了；workspace 导航之前有同样问题。
  → **对本仓库直接相关**：`config/keys.toml` 管的 `prefix+alt+1..9` / `prefix+ctrl+1..9`
  在中文输入环境下之前可能是失效的，0.9.5 修好了。值得实按一次确认。
- **Codex 不再装 turn hook（0.9.5，#5246 #5278）** — 状态检测回到屏幕检测，
  release notes 明确要求 `herdr integration install codex` 更新。已执行（2026-10-19）。
- **关闭 workspace 最后一个 pane 会先确认（0.9.4，#5143 #5173）** — 可用
  `ui.confirm_close = false` 关掉。**`hopen.sh` 的回滚路径要留意**：它在 split 失败时
  调 `herdr pane close` / `workspace close` 并 `|| true` 静默吞错，若确认弹窗导致关闭
  失败，临时 workspace 会泄漏且无提示。
- **主 workspace 关闭时的 worktree 组语义（0.9.4，#4926 #4770）** — 打开同一仓库两次
  不再互相嵌套；worktree 组在 checkout 暂不可用时存活。
- **会话恢复逐个启动（0.9.2，#4102）** + **48 份布局快照**（0.9.2，#4320）——
  见 `~/.config/herdr/session-snapshots/`。

## 六、建议动作（2026-10-19 更新）

1. ~~不用升级~~ → **已升级到 0.9.5**，四个 outdated integration 也已重装（issue #12 已关闭）。
2. ~~在 0.9.3 上重跑 9 布局 smoke~~ → **已在 0.9.5 上复跑通过**（issue #10 已关闭）。
3. 可以考虑但没做的：
   - `[keys] prefix = ["cmd+b", "ctrl+space"]`（第二前缀；install.sh 不会覆盖，已实测）
   - 绑一个 `keys.clear_pane`（0.9.2 新增，默认未绑）
   - 中文输入法下实按一次 `prefix+alt+1`，确认 0.9.5 #5255 的修复在本机生效
4. 仍未处理的仓库内问题：
   - 最低版本约束仍写 `0.8.2`（`install.sh:100,102`、`config/agent-catalog.json`
     的 `min_herdr_version`、`scripts/hopen.sh:29` 注释），与 0.9.x 实际情况不符
   - `install.sh:109` 卸载路径用裸 `rm`，在被可恢复删除包装拦截的环境下 `--uninstall`
     会返回 1（见 `tests/test_agent_audit.py` 中对应的失败用例）

