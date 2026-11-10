# 给测试 Agent 的说明书：奇迹工坊 · GalgameStudio

> 你把这份丢给电脑上的 agent，它能照着测。**先读「0. 红线」，测坏了不可逆。**

---

## 0. 红线（测试前必读）

| 不许 | 为什么 |
|---|---|
| **删 `projects/` 下任何目录** | 那是主人的作品，删了不可逆。宁可新建测试项目 |
| **改 `.state/credentials.json` / `.state/settings.json`** | DPAPI 加密的密钥，动一次就要主人重新配 |
| **对真实云接口重复压测** | 千问生图、百炼 TTS 都是**真花钱**的。真实调用每个接口最多 1 次 |
| **`git push` / `git commit --amend` / 改 `.git`** | 仓库由维护者管，agent 只读不写 |
| **kill 别人的进程** | 只操作 8790 / 8799 两个端口属于自己的进程 |

**允许**：读任何文件、新建测试项目、启停 8790/8799、跑 `python -m unittest`、调本地接口。

---

## 1. 环境

```
项目根        D:\软件\GalgameStudio
后端          http://127.0.0.1:8790      FastAPI + uvicorn
界面          http://127.0.0.1:8799      原生 HTML/JS 静态服务
Python        app\.venv\Scripts\python.exe   (3.11.9)
数据目录      projects\<project_id>\graph.json     项目的唯一真相
运行期状态    .state\（凭据、设置）、.jobs\（任务）
```

**启动**（正常路径，会开 pywebview 窗口）：

```powershell
cd D:\软件\GalgameStudio
Start-Process -FilePath "$PWD\app\.venv\Scripts\pythonw.exe" -ArgumentList 'launcher\galapp.py'
# 等 15~40 秒，然后：
Invoke-WebRequest http://127.0.0.1:8790/api/health -UseBasicParsing   # 期望 {"ok":true,...}
```

**启动（无窗口，纯 API 调试）**：

```powershell
cd D:\软件\GalgameStudio
& 'app\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8790
```

**停止**：

```powershell
$c = Get-NetTCPConnection -LocalPort 8790 -State Listen -ErrorAction SilentlyContinue
if ($c) { Stop-Process -Id $c.OwningProcess -Force }
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force
```

**健康判断的硬标准**（三条都要满足，缺一条就算启动失败）：

1. `GET /api/health` 返回 `{"ok": true, "version": "...", "mock": false}`
2. `http://127.0.0.1:8799/index.html` 返回 HTTP 200
3. launcher.log 里出现 `页面自查 title='奇迹工坊 · Galgame Studio'`

---

## 2. 共用关键词（报 bug 时**只用这套词**）

> 目的：你和维护者说同一件事。括号里是「不要叫成什么」。

### 2.1 数据结构

| 标准词 | 含义 | 别叫成 |
|---|---|---|
| **项目 project** | `projects/<id>/` 一个目录，含 `graph.json` 和 `assets/` | 工程、游戏、存档 |
| **节点图 graph** | `graph.json` 里的 `nodes` + `edges` 整体 | 流程图、剧情树、节点网 |
| **节点 node** | `nodes[]` 的一项，有 `id` / `type` / `lines` / `choices` | 场景、格子、方块 |
| **节点 id** | 如 `n1`、`s007`。历史项目是 `n` 前缀，新项目是 `s/c/e` 前缀 | 编号、ref |
| **出口 exit / 端口 port** | 节点的第 N 条出边，编号从 0 起 | 连线、箭头、分支口 |
| **选项 choice** | `choices[]` 的一项，含 `label` / `to` / `effects` / `condition` | 分支、按钮、选支 |
| **边 edge** | `edges[]` 的一项 = `(from, port, to)` 三元组 | 连线、链接 |
| **台词 lines** | `node.lines[]`，每句有 `who` / `text` / `voice` / `dur` | 对话、文本、字幕 |
| **旗标 flag** | `graph.flags` 里的布尔/整数状态，选项可写 `effects` | 变量、状态、好感度 |
| **章节 chapter** | `agent_state.chapters` 里的分组，画布可按章筛选 | 幕、卷、part |

### 2.2 节点类型（只有 4 种）

| 类型 | 出边数 | 含义 |
|---|---|---|
| `start` | **恰好 1** | 起始节点，全局唯一，历史项目固定 `n1` |
| `scene` | **恰好 1** | 普通剧情推进 |
| `choice` | **2~4** | 分支点（2 或 3 为主，4 只在关键分歧） |
| `ending` | **0** | 结局，必须带 `end_kind ∈ {true, good, bad, alt}` |

### 2.3 生成流水线

| 标准词 | 含义 |
|---|---|
| **骨架 skeleton** | `agent_state.skeleton`。**结构由代码模板生成**，模型只填标题/梗概/选项文字 |
| **填词 fill** | 只填 `lines`（台词），**不许改结构** |
| **骨架冻结 SKELETON_FROZEN** | 骨架一旦生成，任何改结构的写入都会被拒，返回这个错误码 |
| **五个 agent** | `writer`（剧情）/ `art`（图）/ `voice`（配音）/ `build`（装配）/ `review`（验收） |
| **阶段状态 stage status** | 每个 agent 的运行状态：`real` / `mock` / `off` / `failed` |
| **降级影响 impacts** | 上游降级时，下游受什么影响。写在 `export_report.json` |

### 2.4 不变量（判断「图合不合法」用）

**写入时强制（I1–I8）**——违反就返回 422：

```
I1  edges == edges_from_nodes(nodes)      边必须是 choices 的投影
I2  节点 id 全局唯一
I3  出边数：start/scene=1, choice=2~4, ending=0
I4  choice 的 port 连续 0..n-1，无空洞
I5  同一 (from, port) 最多一条边
I6  从起始节点可达全部节点
I7  无环
I8  ≥1 个 ending，且 ≥1 个 end_kind == "true"
```

**只在生成时强制（I9–I11）**——保存旧图时不检查：

```
I9   choice_count / node_count <= choice_density * 1.5
I10  分叉深度 <= depth_max
I11  每条侧枝在 rejoin_max 步内汇合到入度>=2 的节点，或到达 ending
```

> **报 bug 时请写明是哪一条**，例如「I6 违反：不可达 3 个节点」。

### 2.5 错误码（见到就照抄，别翻译）

`SKELETON_FROZEN`、`GRAPH_REJECTED`、`INVALID_NODE`、`INVALID_ARGUMENTS`、`TOOL_FORBIDDEN`、`PROJECT_MISMATCH`、`NODE_NOT_FOUND`、`IO_ERROR`、`PROVIDER_ERROR`、`NEED_WAV`、`INVALID_KIND`

---

## 3. 功能清单（逐项打勾）

> 每项给「预期」和「怎么验」。**测不出预期就记 bug**。

### 3.1 启动与健康

- [ ] **S1** launcher 启动后 40 秒内出现标题为 `奇迹工坊 · Galgame Studio` 的可见窗口
      预期：窗口宽 >1000px、位置在屏幕内（不在 ±30000 那种屏幕外坐标）
- [ ] **S2** `GET /api/health` 返回 `ok:true`
- [ ] **S3** `GET /api/projects` 返回项目列表，每项有 `id` / `name` / 节点数
- [ ] **S4** 重复启动 launcher：不会起第二份后端，日志出现「已经有一个窗口在运行」
- [ ] **S5** 关闭窗口后后端是否退出（当前设计：**故意常驻**，日志有「后端保持运行」）→ 记行为，不算 bug

### 3.2 项目管理（视图 `projects`）

- [ ] **P1** 新建项目：填名字点「新建项目」→ 列表出现新卡片，`projects/<id>/graph.json` 被创建
- [ ] **P2** 打开项目：点「打开」→ 切到 editor 视图，画布渲染出节点
- [ ] **P3** 删除项目：**只在测试项目上做**。删完列表消失、目录移除
- [ ] **P4** 刷新列表：不丢项目、不重复
- [ ] **P5** 项目名显示：重启后能记住上次打开的项目（localStorage `gal_project_id`）

### 3.3 接口配置（视图 `setup`）

- [ ] **C1** 六个接口卡片存在：通义千问生图 / 百炼语音合成 / 大语言模型剧本 / ComfyUI / 本地 TTS / 本地 LLM
- [ ] **C2** 「测试连接」对每个卡片都返回明确结果（成功带延迟，失败带原因）
- [ ] **C3** 填了 API Key 点保存后，`GET /api/providers` 不回显明文 key（应返回掩码）
- [ ] **C4** 空 key 保存不报错（保留后端默认）
- [ ] **C5** 未配置的接口不应让整个流程卡死（应降级为 mock 并在报告里标出来）

⚠️ **真实调用限量**：`POST /api/providers/{kind}/test` 每个 kind 最多调 2 次。

### 3.4 生成（视图 `setup` → editor）

- [ ] **G1** 填需求点「开始生成」→ 有 SSE 日志流（`GET /jobs/<id>/events`），能看到 phase 推进
- [ ] **G2** 生成完成后 `graph.json` 的节点数在目标的 ±20% 内
- [ ] **G3** 生成完成后跑校验：`structure_problems(graph)` 返回**空列表**
- [ ] **G4** 每个 `choice` 节点选项数 ∈ [2,4]，且 `to` 都指向存在的节点
- [ ] **G5** 非 ending 节点出边数都是 1 或 2~4，**没有 0**
- [ ] **G6** 至少 1 个 `end_kind == "true"` 的结局
- [ ] **G7** 生成中断后能否恢复（`GET /projects/{id}/resume`）
- [ ] **G8** 取消任务（`POST /jobs/{id}/cancel`）后进程不残留、状态回到可再生成

### 3.5 画布编辑（视图 `editor`）

- [ ] **E1** 节点可拖动，位置存到 `nodes[].pos`，刷新后位置保留
- [ ] **E2** 连线：从出口拖到目标节点，`edges` 随之更新
- [ ] **E3** 属性面板改台词 → 保存 → 重新打开，文字是新的（多句节点**不能只留第一句**）
- [ ] **E4** choice 加/删选项：端口编号重排后 `edges` 仍与 `choices` 一致
- [ ] **E5** 换节点类型（scene↔choice↔ending）时旧出口被正确清理，不留下悬空边
- [ ] **E6** 章节目录筛选：只看某章时不丢章外节点和跨章边
- [ ] **E7** 撤销/重做，撤销后图与 `graph.json` 一致
- [ ] **E8** 手动保存（`PUT /projects/{id}/graph`）：合法图成功；**故意构造违规图**（如 start 加第二个出口）→ 必须 422 且错误信息列出全部问题

### 3.6 试玩播放器（视图 `play`）

- [ ] **R1** 点击「试玩」从起始节点开始播
- [ ] **R2** 台词逐句推进，点击可跳过打字机
- [ ] **R3** 走到 `choice` 节点弹出选项，**按钮数 == 该节点 choices 长度**，且 ≤4
- [ ] **R4** 选项文字是真实内容，**不出现「选项 A/B/C/D」这种占位**
- [ ] **R5** 点每个选项都能走到下一个节点，不出现「这个节点后面没接线了」
- [ ] **R6** 走到 `ending` 节点显示结局页，标题按 `end_kind` 区分（真/坏/隐藏）
- [ ] **R7** 走完整条主线不卡死路；遇到死路时能退回上一个选择
- [ ] **R8** 控制台无 `[player] 选项数异常`

### 3.7 素材与角色

- [ ] **A1** 素材库 `GET /projects/{pid}/library` 列出背景/立绘/语音
- [ ] **A2** 上传参考图后能在角色上选中（`POST /projects/{pid}/references`）
- [ ] **A3** 角色表增删改（`/characters`），改了之后节点 `chars[]` 引用不报错
- [ ] **A4** 语音列表 `/voices`，按素材打标签 `/voices/label-by-asset`
- [ ] **A5** 抠图：立绘去白底后边缘不发灰、透明区域不被二次处理

### 3.8 导出（视图 `export`）

- [ ] **X1** 导出 Ren'Py：产物含 `script.rpy`，语法能被 Ren'Py 校验（至少结构正确）
- [ ] **X2** 导出网页：`export/web/index.html` 能打开并播放
- [ ] **X3** 导出 Unity：产物含 `StreamingAssets/graph.json` 和 `Scripts/`
- [ ] **X4** `export_report.json` 含 `stages` / `degraded` / `impacts` / `gates` / `dependencies` / `failures`
- [ ] **X5** 有降级时报告里能看到具体影响（例如「art: 5 张立绘使用占位图」）

### 3.9 修复工具

- [ ] **F1** `GET /projects/{id}/repair-from-skeleton` 只预览不写盘
- [ ] **F2** `POST` 带 `{"apply": true}` 才真写，且写了备份
- [ ] **F3** 对没有 skeleton 的项目返回明确原因，不崩

---

## 4. 什么**不算** bug（别报）

| 现象 | 说明 |
|---|---|
| 部分旧测试失败 | 基线就是 **136 tests / 3 失败 / 6 错误**（历史遗留），不超过这个数不算新问题 |
| `projects/` 里有坏图项目 | 实测 666 个项目里 **38 个**违反 I1–I8（约 5.7%），其中多数无骨架修不了，属历史数据 |
| 项目总数每次跑都变 | `_qa\_test_skeleton_e2e.py` 会新建项目，跑一次 +1 |
| 关闭窗口后后端还在 | 设计如此（复用后端，避免每次重开） |
| `dist/` 里是旧 exe | dist 是构建产物，源码改动不自动进 exe |
| 界面首次加载慢 1~2 秒 | 要下 1.3 MB 静态资源 |
| `ui/assets/title/_edgeprof*` 这类目录 | Edge 的临时 profile，已在 .gitignore |
| 真实接口返回降级为 mock | 没配 key 时的预期行为，**要看报告里有没有标出来** |
| 单测命令加 `-t .` 报 ImportError | `tests/` 没有 `__init__.py`，用 `-s tests` 就够了 |

---

## 5. Bug 报告格式（**必须按这个写**）

```markdown
### [P?] 一句话标题
- **复现步骤**：1. … 2. … 3. …
- **预期**：…
- **实际**：…
- **证据**：命令原文 / HTTP 状态码 / 报错原文 / 文件路径   ← 不贴证据的 bug 直接打回
- **不变量**：违反了 I几（如果和图结构有关）
- **影响面**：能不能继续用？丢了什么数据？
```

**优先级**：

| 级别 | 标准 |
|---|---|
| **P0** | 数据丢失 / 启动不了 / 图损坏后仍被写入 |
| **P1** | 核心流程断（生成失败、导出失败、播放器卡死） |
| **P2** | 功能可用但行为错（选项数不对、文字丢了、位置没存） |
| **P3** | 体验问题（慢、丑、文案拗口） |

---

## 6. 三分钟冒烟测试（先跑这个）

```powershell
cd D:\软件\GalgameStudio
$py = 'app\.venv\Scripts\python.exe'

# 1. 单元测试（基线：136 tests / 3 失败 / 6 错误）
#    ★ 必须用 -s tests，不能加 -t .（tests/ 没有 __init__.py，加了会 ImportError）
& $py -m unittest discover -s tests 2>&1 | Select-Object -Last 4

# 2. 全量图校验（扫所有项目对照 I1–I8）
& $py '_qa\_scan_gate2.py' 2>&1 | Select-Object -First 4

# 3. 骨架 e2e（180 节点，约 5 秒）
& $py '_qa\_test_skeleton_e2e.py' 180 2>&1 | Select-Object -Last 6

# 4. 播放器渲染检查（用后端真实图算按钮数，需要 8790 在线）
& $py '_qa\_verify_player.py' 2>&1 | Select-Object -Last 12
```

四项都跑完，比对（**以下是 2026-11-10 实测基线**）：

| 项 | 期望 |
|---|---|
| 单测 | **136 tests / 3 failures / 6 errors**，失败数不增加 |
| 全量图校验 | 项目总数会变；**「会被拒」的比例应在 5~6%**（实测 666 项目 / 38 拒） |
| e2e | 输出 `全部通过 ✓`、`断头节点 0 个` |
| 播放器 | 两个项目的按钮数分布全在 `{2,3,4}`，`占位式 label: 无` |

> ⚠️ **第 3 条会新建一个项目**（`projects/p_xxx`），所以第 2 条的项目总数每次跑都会 +1。
> 判「回归」不要看绝对数，看**被拒比例**和**新增的失败原因**。

---

## 7. 关键文件索引（读代码时用）

```
app/engine/graph.py         normalize_graph / edges_from_nodes / structure_problems / apply_graph
app/engine/skeleton.py      SkeletonNode / to_graph_stub / validate_skeleton（I9–I11）
app/engine/contracts.py     阶段契约 STAGE_CONTRACTS
app/engine/checkpoint.py    checkpoint 保存/恢复/verify
app/agents/outline.py       build_template（代码定拓扑）/ 分批填槽
app/agents/fill.py          只填台词
app/agents/tools.py         AgentTools / _commit / write_nodes / 各类校验
app/agents/loop.py          writer 循环 / FILL_ROUNDS
app/contracts.py            Node / Choice / Graph 的 pydantic 契约
app/mock_api.py             全部 HTTP 路由
ui/app.js                   画布 / 播放器 / 属性面板
ui/graph-editor.js          画布出口数与 choiceLabels
launcher/galapp.py          启动器（后端 + 界面 + 窗口）
tools/sync_to_github.py     一键同步到 GitHub
_qa/                        维护者的验证脚本（不进版本库）
```
