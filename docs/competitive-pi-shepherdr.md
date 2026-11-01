# 竞品分析:pi-shepherdr vs herdr-recipes

- 分析日期:2026-10-28
- 分析对象:`@howaboua/pi-shepherdr` v0.2.24(Igor Warzocha,[howaboua-pi-stuff](https://github.com/IgorWarzocha/howaboua-pi-stuff))
- 我方版本:herdr-recipes(见根 `VERSION`)
- 方法:读对方源码 + 在本机 Herdr 实测(spawn/多 agent 并行/关 tab)

## TL;DR

两者做同一件事的**两种范式**:

- **pi-shepherdr** 把编排做成「模型可调用的结构化 `agents` API + 确定性状态机」,智能压在代码里。
- **herdr-recipes** 把编排做成「自然语言规程 + 现场核验的 prompt 自治」,智能留在 prompt 里。

一句话判断:**她用代码换确定性,我们用 prompt 换无隐藏状态。** 这是确定性与鲁棒性的经典交换,各有胜负手,不是简单的谁替代谁。

## 1. 范式差异(根本)

| | pi-shepherdr | herdr-recipes |
|---|---|---|
| 形态 | TypeScript Pi 扩展,`pi.registerTool` 注册 `agents` 工具 | Shell 脚本 + herdr 配置 recipes,一键安装 |
| 编排逻辑在哪 | 代码(spawn/assign/settlement 状态机) | prompt(coordinator-prompt.md 228 行规程) |
| 协调者是谁 | 无独立协调者,主会话直接调 `agents` 工具 | 一个对话式 coordinator(codex/pi),靠 prompt 驱动 |
| worker 怎么被指挥 | 结构化 action + 参数 | 协调者自己拼 `herdr` CLI 命令 |
| 失败兜底 | 代码保证(settlement 核验/重试/去重) | prompt 约定 + `pane list`/`agent list` 现场核验 |

## 2. 核心机制逐项对比

| 维度 | pi-shepherdr | herdr-recipes | 谁更优 |
|---|---|---|---|
| 结果就绪信号 | **settlement**:读 worker session 文件,核验后 resolve/注入 | **DONE 标记**:worker 打印 `DONE <任务ID>`,`pane wait-output` 阻塞 | 她(确定性核验) |
| 等待方式 | blocking promise 或异步自动推送,明确禁 polling | wait-output 首选 / agent wait 兜底,同样禁 sleep 盲等 | 平手 |
| 布局 | `new_tab` 单 pane,自己不 split(实测确认) | **主动 `pane split`,一个 tab 多 pane**,按 issue 分 tab | **我方** |
| 生命周期 | 硬状态机,每步 persist | prompt 约定 + 现场核验,无隐藏状态 | 各有取舍 |
| worker 异常恢复 | settlement 检测 blocked/error,逻辑薄 | **极详细**:原会话恢复 2 次→重建 1 次→报告阻塞 | **我方** |
| name 唯一性 | 代码保证(`reserveAgentName` 自动去重) | prompt 约定 + 启动前核查重名 | 她 |
| 模型绑定 | profile **硬绑**(general=gpt-6-sol) | 沿用各 agent 原生配置 | **我方(灵活)** |
| worker 预算 | 无,可无限 spawn | max_workers=4(prompt 约束) | **我方(有约束)** |
| 隔离/交付 | 无专门规程 | worktree 隔离、测试现场隔离、整合交付、执行记录交接 | **我方** |
| 跨机器 | 原生 SSH fleet | 本地为主 | 她 |
| 渲染层 | renderCall/renderResult 渲染成 TUI | 纯终端 | 她(体验) |

## 3. 亮点深挖:pi-shepherdr 的 settlement 机制

这是她最值得学的部分。核心代码:`src/settlement/{index,collector,delivery}.ts` + `src/agents-work.ts`。

### 3.1 数据流

**阻塞路径(blocking=true):**
1. `dispatchAgentWork` → `delivery.claim(attempt)` 返回 Promise,存入 `claims[attemptId]`
2. send message 给 worker pane
3. worker 干活,herdr 状态变 done → monitor 调 `settlement.report(...)`
4. `report` → `collector.collect` 读 worker session 核验 → `state.complete` → `deliverSettlement`
5. `deliverSettlement` 找到 `claims[attemptId]` → resolve Promise
6. 主会话 `await settlement` 拿到结构化 `{reply, status, blocked_on, worker_error, ...}`

**异步路径(blocking=false):** 1–4 同,但无 claim;`deliverSettlement` 找不到 claim → `injectAgentEvent` 把事件注入主会话(即使主会话已 reply 也会送达)。

### 3.2 五个真正强的点

1. **结果来自 worker 的 session 文件,不是终端文本。**
   `collector` 读 `sessionPath(current)` 的 Pi session(jsonl),解析出结构化 `assistant/input/extensionMessages`。终端的 ANSI/换行/分页零污染。这是她比"读终端"干净一个数量级的根本原因。

2. **三重核验防"假完成"(精华)。**
   `report` 不轻信 worker 的 done 状态,逐项核验:
   - `terminal_id` 一致(pane 没被重建)
   - `task + attemptId` 一致(还是这个任务这次尝试,不是残留)
   - **持久化核验**:`assistantAfterInput === true` 且 `input.id !== expectedUser.after`(提交的输入和回复都落盘了,且不是发送前就有的旧 input);`requireNewReply` 确保 `reply.id !== lastAssistantId`(是新回复)
   解决竞态:worker 显示 done 但 session 未写盘、或写的是上一轮回复。

3. **指数退避 + 有界窗口。**
   `[250, 500, 1000, 2000, 4000]ms`,共约 7.75s。核验不过就退避重试,超时才 notify "settled, but ... after N seconds" 并 reject。有界,不无限等。

4. **per-attempt claim,恰好一个消费者。**
   `SettlementDelivery` 按 attemptId 存等待者;阻塞 Promise 与事件注入**二选一**,不重复交付;abort 可取消。

5. **去重 + blocked 竞争处理。**
   `reporting` Map 防同一 attempt 重复 report;`pendingBlocked` 处理"先 done 又 blocked"的竞争,最终以 blocked 为准重跑。

### 3.3 代码位置索引

| 机制 | 文件 |
|---|---|
| 工具面/action 分发 | `src/agents-tool.ts` |
| 派活 + 阻塞/异步分流 | `src/agents-work.ts`(`dispatchAgentWork`) |
| settlement 核验/重试/去重 | `src/settlement/index.ts`(`SettlementReporter.report`) |
| 读 worker session | `src/settlement/collector.ts` |
| 双路交付(阻塞/异步) | `src/settlement/delivery.ts` |

## 4. 结构性优劣(胜负手)

**pi-shepherdr 的强项:** settlement 确定性结果回收、结构化错误字段、name 唯一性代码保证、跨机器 fleet、TUI 渲染。

**pi-shepherdr 的弱项(已实测复现):**
- **硬状态机的同步税**:外部 `herdr tab close` 关 pane 后,pi session 的 monitor-state 快照不同步,`herdr agents` 面板残留 idle ghost,需 `/reload` 才清。根因是"持久化快照 vs 实时 pane"两套数据源,外部强关时对不齐。这是所有硬状态机编排的必然成本。
- `new_tab` 单 pane,布局不灵活。
- profile 硬绑模型。
- 无 worker 预算控制(可无限 spawn)。
- worker 异常恢复规程单薄。

**herdr-recipes 的强项:** 布局灵活(主动 split/按 issue 分 tab)、无隐藏状态(结构上不可能出 ghost)、worker 异常恢复规程详尽、预算/worktree/测试隔离/整合交付等工程化规程完整、模型不硬绑。

**herdr-recipes 的软肋:** 全靠模型自觉执行 prompt——不 wait-output 而 sleep、不打印 DONE、不核查重名,规程就退化;无结构化工具面,协调者手拼 CLI 靠现场核验兜底;回报靠 `agent read` 读终端文本,不如 session 结构化字段干净。

## 5. 可操作建议(按优先级)

### 高:回报格式加结构化尾部块(吸收 settlement 优势)
现状 DONE 只有一行,只证明"打印了这行",不证明"回复完整/是当前任务的新回复"。约定 worker 在结论前打印:

```
DONE <任务ID>#<序号> status=done|blocked path=<交付路径> summary=<一句话>
```

- `<任务ID>` 你已经做了任务相关性核验。
- `<序号>` 全局递增,协调者只认"当前任务 + 未见过的最大序号+1"——近似她的 `attemptId + requireNewReply`,过滤旧回复。
- `status/path/summary` 结构化字段——近似她的 `settlementResult`,`wait-output` 到块后再 `agent read --source recent` 读详情。

### 中:把"现场核验"固化为差异化(ADR)
她的 ghost bug 证明了软方案无隐藏状态的结构性优势。建议补一篇 ADR(如 `0004-live-verification-over-state-machine`),明确:herdr-recipes 选择"现场核验 + 无持久化编排状态"是相对硬状态机的刻意取舍,代价是接受模型不遵守时的退化,收益是不会出现快照与实时 pane 不一致类 bug。

### 低:max_workers 加脚本级硬闸
你目前预算是 prompt 约束。可在 `hstatus.sh`/启动路径数存活 worker,超预算时让 `agent start` 直接失败并提示,把软约束升级为硬闸。她的无限 spawn 是反例。

## 6. 为什么有些不能照抄

| 她想做 | 我们为何不照抄 |
|---|---|
| 读 worker session 文件核验 | 我方 worker 可能是 claude/codex/pi 多 kind,session 格式不统一,读不了。只能用终端文本协议近似。 |
| 硬状态机保证结果回收 | 会引入隐藏状态,正是 ghost bug 的根源,违背我方"现场核验"的差异化定位。 |

结论:**吸收 settlement 的"核验思想"(任务相关性 + 新鲜度 + 完整性),不抄其"读 session + 状态机"的实现。**

## 附录:版本与依赖

| | pi-shepherdr |
|---|---|
| 版本 | 0.2.24 |
| 强依赖 | Herdr(终端多路复用器)、Pi 1.2.0+、Node 22.18+ |
| 可选 | `@howaboua/pi-codex-conversion`(Code/Notebook Mode 适配,缺失自动跳过) |
| 安装方式 | Pi npm 包,`settings.json` 的 `pi.packages` 追加 `npm:@howaboua/pi-shepherdr` |
