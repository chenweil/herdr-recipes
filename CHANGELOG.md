# Changelog

本项目所有值得记录的改动都写在这里。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

版本号存在仓库根的 `VERSION` 文件里，所有脚本通过 `-v` / `-V` / `--version` 读同一份。

## [0.4.0] - 02-09-2026

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

## [0.3.0] - 28-08-2026

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
  `hopen.sh`、`hopen-once.sh`、`herdr-pane-switch.sh`、`herdr-pane-switch.py`、
  `install.sh` 都支持 `-v` / `-V` / `--version`。
- 本 CHANGELOG。

### 变更

- `_panes_for` 从 `hopen-once.sh` 移到 `hopen.sh`，两个脚本共用同一个查表。
- `_h_build_layout` 参数扩展到 5 个：`layout`、`ws_label`、`cwd`、`tab_label`、
  `pane_names`。后两个是新增，留空则走默认推导。
- `hopen-once.sh` 的 kind 数超出 layout pane 数时，多余部分不再被丢弃，
  而是当 pane name 用。

## [0.2.0] - 27-08-2026

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

## [0.1.0] - 27-08-2026

### 新增

- **`hopen.sh`**：按代号（`12` / `21` / `22` / `13` / `31` / `111`）开 pane 布局，
  开在新 workspace 里，不影响当前 ws。split 失败会回滚已开的 pane 并关掉 ws。
- **`hopen-agents.conf`**：按 layout + 视觉位置配置每个 pane 起哪个 agent，
  可选带 prompt。conf 缺失 / 段缺失 / kind 没装都只跳过对应 pane，不影响布局。
- **Kind 别名**：`op` → `opencode`、`cc` → `claude`、`cd` → `codex`、`pi` → `pi`；
  未列出的原样透传，herdr 新增 kind 时不用改代码。
- **`herdr-pane-switch.py` / `.sh`**：切到当前 workspace 的第 N 个 pane。
- **`config/keys.toml`** + **`install.sh`**：一条命令装好键位。
  `prefix 1..6` 切 pane，`prefix alt 1..6` 开布局并起 agent，
  `prefix ctrl 1..6` 开裸布局。install 幂等，用 marker 块管理 `config.toml`，
  块外的配置不动；支持 `--uninstall` 回滚。
