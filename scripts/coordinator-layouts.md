# 用户指定当前 tab 时的布局

用户明确要求当前 tab 准备 worker 时读取。保留已有 pane/agent，优先复用登记过的合适
worker；原有布局不强制重排。从单个主 pane 扩展时，主 pane 保留左侧 50%，worker
共用右侧 50%。每步引用真实返回的新 ID，用 `--ratio 0.5 --cwd <项目目录> --no-focus`：

| 新增 worker | 分裂哪个 pane | direction | 新 pane |
|---|---|---|---|
| 第 1 个 | 主 pane | right | A |
| 第 2 个 | A | down | B |
| 第 3 个 | B | right | C |
| 第 4 个 | A | right | D |

三个 worker 是右侧上一个、下两个；四个是上二下二。超过四个或空间不足时，按用户
要求选择可读布局，必要时说明空间冲突，不连续挤成窄条。实际位置、比例与图示通过
`pane layout --pane <主 pane ID>` 的 tab_id 和所有团队 pane 的 rect 查询核验。
该布局只是当前 tab 分支；默认工作 tab 的布局由任务需要决定。
