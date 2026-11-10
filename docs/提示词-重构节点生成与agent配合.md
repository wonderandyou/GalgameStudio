# 任务：重构 GalgameStudio 的「节点生成」与「多 Agent 配合」

> 这份提示词要求你做两件事：
> **Part A —— 把「节点生成」从「模型自由画图」改成「代码保证结构、模型只填内容」**
> **Part B —— 把 writer / art / voice / build / review 五个 agent 的配合逻辑理顺**
>
> 两部分可以分开交付，但建议一起看 —— 因为节点生成出来的「契约」正是各 agent 配合的接口。

---

## 0. 背景：现在的问题

项目在 `D:\软件\GalgameStudio`，是一个 Galgame（视觉小说）自动生成工具：

```
用户填一句需求 → writer 出剧情骨架+台词 → art 出背景和立绘 → voice 配音 → build 装配 → review 验收
```

**问题一：节点生成不稳定**

现在的流程是「让模型自由画一张图（分批）→ 代码校验 → 不合格就带原因重试 → 3 次都失败整轮报废」。
模型几乎每次都会犯一种**结构错误**，而且每修一个下一个又冒出来：

| # | 实测错误（日志原文） | 模型为什么犯 |
|---|---|---|
| ① | `骨架节点 ref 重复：k161` | 写着写着忘了自己写过 |
| ② | `骨架有环，k061 无法完成拓扑排序` | 两条支线互相回连 |
| ③ | `k156 是断头节点，非 ending 必须有出口` | 写到最后一个节点忘了给出路 |
| ④ | `k120 第 2 个出口标成 bad，但 k180 的 end_kind 是 true` | 出口上的标注是猜的 |
| ⑤ | `k056 的分支深度 3 超过上限 2` | 它不知道「嵌套」怎么数 |
| ⑥ | `骨架节点数 150 超出目标 100 的 ±10%` | 控制不好数量 |
| ⑦ | `LLM 未返回合法 JSON（共 N 字符……）` | `max_tokens` 截断 |

**问题二：agent 之间的配合是隐式的**

五个 agent 之间靠 `graph.json` 这个巨型 dict 传递所有东西，
**没有明确的「产出契约」**，导致：

- writer 出的东西 art 能不能用、要不要等，全靠时序巧合
- 某个阶段降级了，下游不知道，还在按「有真产出」的假设干活
- review 放在最后，发现问题时代价已经很大

---

## 1. 代码位置

```
D:\软件\GalgameStudio\
├─ app\engine\skeleton.py     骨架数据结构 + 校验
│                              （SkeletonNode / Skeleton / validate_skeleton /
│                               to_graph_stub / skeleton_layout / _branch_depth）
├─ app\engine\pipeline.py     阶段调度（writer/art/voice/build/review）+ 降级（degrade）
├─ app\agents\registry.py     五个 agent 的定义（stage / skill / tools / prompt）
├─ app\agents\tool_schema.py  各 agent 的工具白名单与 JSON Schema
├─ app\agents\tools.py        工具实现（AgentTools，两千多行）
├─ app\agents\loop.py         总调度（骨架 → 填充 → 美术 → 装配 → 验收）
├─ app\agents\outline.py      骨架生成（mock_skeleton 纯算法 / _model_skeleton 模型路径）
├─ app\agents\fill.py         填充台词（只改 lines / bg_prompt / chars）
├─ app\providers\llm.py       调模型（call_json / call_with_tools）
├─ app\contract.py            图数据契约（Node / Choice / Graph，pydantic，extra='forbid'）
├─ app\skills\                各 agent 的提示词文档（writer/art/voice/build/review/ui）
└─ tests\test_skeleton.py     骨架相关单测
```

**五个 agent**（`registry.py::AGENTS`）：

| agent | stage | 工具数 | 职责 |
|---|---|---|---|
| writer | writer | 15 | 出骨架、填台词、写角色 |
| art | art | 7 | 定风格、出背景、出立绘、出界面 |
| voice | voice | 3 | 配音、回写时长 |
| build | build | 3 | 装配、导出 |
| review | review | 2 | 验收 |

---

## Part A：节点生成（模板方案）

### A1. 目标

**让「结构」由代码保证，模型只负责内容。**

要求：

1. **180 节点、9 批、任意模型输出**（上面 ①~⑦ 全叠上），**一次生成必须成功**，
   产出合法 DAG。
2. 结构硬指标一条不许松：
   - 有且只有一个 `start`（且是第一个节点）
   - 所有节点从 `start` 可达
   - 无环（拓扑排序能排完）
   - 非 ending 必须有出口（**0 断头**）
   - 至少一个 `end_kind='true'` 的结局
   - `flavor` 分支的所有路线必须汇合到同一个节点
3. 模型仍然要写：剧情、标题、摘要、分支选项文案、角色设定。

### A2. 核心做法

```
[代码]  生成一张拓扑合法的大树骨架（DAG、有汇合、有结局）
   ↓   每个节点只留「槽位」：title / summary / 分支标签
[模型]  按章节读槽位，把剧情填进去（不碰结构）
   ↓
[代码]  合并 → 校验（此时几乎不可能失败，因为结构是代码造的）
```

**可行做法**：把现有的 `mock_skeleton()`（`outline.py`，纯算法、已经能产出合法 DAG）
泛化成模板生成器 `build_template(*, target_nodes, depth, endings, key_branches, flavor_branches)`，
然后：

- 模板节点的 `title` / `summary` 先放占位
- 把槽位列表发给模型（**只给 ref、kind、hint，不给 type/to**）
- 模型返回 `{ref, title, summary}` 列表
- 代码按 ref 合并，**结构字段一律以模板为准**

**模型多给/少给/写重/乱写结构字段 → 一律丢弃或兜底，不报错。**

### A3. 分批

- 模板可能有 180 个槽位，一次给太多模型会漏
- 建议**按章节分批**（每批 15~25 个槽位），每批带上批的 `handoff`（前情提要）
- 单批失败只影响那批内容，**结构早已固定，不会整轮报废**
- 每批最多重试 2 次，仍失败就用兜底文案占位

### A4. 不要做的

- ✗ 继续加校验、继续重试（追不上模型的不确定性）
- ✗ 事后修断头/不可达（**试过，会连锁**：补断头把下游节点抛成不可达，
  补不可达又要改 start 的出边，而 scene 只能有一个出口 → 越修越乱）
- ✗ 让模型碰 `type` / `to` / `ref`

### A5. 必须保留

- `MOCK` 模式（`mock_skeleton` 行为兼容，单测依赖它）
- 检查点续跑（`checkpoints/ck-*.json`，重启后从已冻结骨架续写）
- `meta.structure_warnings`（修复类操作往这里记）
  ⚠ `meta` 是 `additionalProperties: False`，**加新字段会让 `write_skeleton` 报
  `INVALID_ARGUMENTS`**（踩过）
- `choices[].kind`（`app/contract.py::Choice` 的可选字段，
  `to_graph_stub` 按目标节点自动填 `chapter` / `true` / `good` / `bad` / `alt`）

---

## Part B：Agent 配合

### B1. 现在的配合方式（问题所在）

```
loop.py 按顺序调：
  1. writer: generate_skeleton() → write_skeleton() → fill_segment() 分批填台词
  2. writer: direct_production() 出导演排产表（哪些 bg_key、哪些立绘）
  3. art: set_style → 背景 → 立绘（base → 差分） → 界面
  4. voice: batch_synthesize（回写 dur）
  5. build: 装配
  6. review: 验收
```

**所有数据都在 `graph` 这一个巨型 dict 里传递**：

```
graph['nodes'][i]['lines']        ← writer 写，voice 读
graph['nodes'][i]['bg']           ← writer 写占位，art 写真图
graph['nodes'][i]['chars']        ← writer 写 pose，art 出图
graph['agent_state']['art']       ← art 写自己的报告
graph['agent_state']['ui']        ← art 写界面
```

**问题**：

1. **没有「产出契约」**：下游凭什么知道上游产出了什么？靠读 dict 猜
2. **降级不透明**：`pipeline.degrade(stage, reason)` 只改了 `stages[stage]='mock'`，
   下游不看这个字段就继续按「有真产出」干活
3. **review 在最后**：出问题时代价已最大
4. **writer 一肩挑**（骨架 + 填充 + 导演排产），失败面太大
5. **失败重试散落在各 agent 里**，没有统一的「这一阶段算过没过」的判断

### B2. 目标：定义清楚的「阶段契约」

给每个阶段定义一份**显式的产出契约**（建议放 `app/engine/contracts.py` 或
`pipeline.py` 里），一项一项可校验：

```python
STAGE_CONTRACTS = {
    'writer': {
        'produces': ['nodes(骨架)', 'nodes[].lines', 'nodes[].bg.prompt',
                     'nodes[].chars', 'characters', 'production_plan'],
        'requires': [],
        'gate': '骨架 0 断头、全可达、无环、至少 1 个 true 结局',
    },
    'art': {
        'produces': ['nodes[].bg.asset(真图)', 'nodes[].chars[].pose(真图)',
                     'graph.style', 'agent_state.ui'],
        'requires': ['writer'],
        'gate': '每个 bg_key 有且一张真图；每角色至少有 base；无占位残留',
    },
    'voice': {
        'produces': ['nodes[].lines[].voice', 'nodes[].lines[].dur'],
        'requires': ['writer'],
        'gate': '每句都有 voice 或明确降级标记；dur > 0 且是真实时长',
    },
    'build': {
        'produces': ['导出产物'],
        'requires': ['writer', 'art'],
        'gate': 'validate_graph 通过',
    },
    'review': {
        'produces': ['agent_state.review'],
        'requires': ['build'],
        'gate': '——',
    },
}
```

**并且**：

1. **每个阶段结束后跑一次自己的 gate**，不合格就**明确标记** `stages[stage]='failed'`，
   而不是让下游踩雷
2. **下游开始前检查 `requires` 里各阶段的 `stages` 值**：
   - `real` → 正常干
   - `mock` → 用占位，**并在最终报告里写明哪一步是占位**
3. **review 前移**：每个阶段自己的 gate 就是「小 review」，最后一轮 review 只做汇总

### B3. 降级要显式，不要靠猜

现在 `pipeline.degrade(stage, reason)` 只写 `stages`。建议补：

```python
async def degrade(self, stage, reason, *, impact=''):
    """标记某阶段降级。impact 写清「对下游意味着什么」。"""
    self.stages[stage] = 'mock'
    self.degraded.setdefault(stage, reason)
    self.impacts[stage] = impact      # 新增：下游看到 impact 就知道该怎么应付
```

例：

- `art` 降级 → `impact='所有 bg 和立绘都是占位图，导出时用纯色块'`
- `voice` 降级 → `impact='所有台词都是静音占位，时长按 4.5 字/秒估算'`

**最终导出报告里必须写明每个 stage 的 `stages` 值和 `impacts`。**

### B4. 各 agent 的 skill 文档要和代码对齐

`app/skills/*/SKILL.md` 是各 agent 的提示词，现在有几处和代码不一致：

- 工具白名单（`tool_schema.py::tool_names(agent)`）里没有的工具，skill 里提到了
  → 模型会拿到 `TOOL_NOT_ALLOWED`
- 已有审计脚本：`tools/_audit_skills.py`，**改完必须跑它**，要求「没有硬问题」

### B5. 交付 Part B 时要回答的问题

写进 `说明.md`：

1. 每个阶段「产出什么、消费什么、gate 是什么」
2. 某个阶段降级时，下游怎么感知、怎么应付
3. 五个 agent 的执行顺序有没有可以并行的地方（比如 art 和 voice 互不依赖，能不能同时跑）
4. 失败重试的边界在哪（哪些能重试、哪些直接降级、哪些直接报错）

---

## 2. 硬性纪律（踩出来的，别犯）

### 2.1 别把「模型的可信度」和「结构的硬指标」放在一条线上

| 类别 | 例子 | 做法 |
|---|---|---|
| **硬指标**（代码能保证） | 断头、环、可达、唯一 start、结局存在 | 代码保证，校验兜底 |
| **模型的猜测**（低可信） | ref 重复、id 写错、出口标错、批次漏写 | **丢/纠正，不报错** |

### 2.2 报错要能区分「截断」和「格式错」

`app/providers/llm.py::call_json` 已经改成带**总长度 + 结尾 200 字**，保留这个做法。

### 2.3 token 上限要留余量

中文 JSON 一次写 20 个节点约 4000~6000 token。
当前：`OUTLINE_BATCH_SIZE=20`、`max_tokens=8192`，`fill.py` 也是 8192。**别往回调小**。

### 2.4 改完必须真跑

```powershell
cd D:\软件\GalgameStudio
$env:PYTHONPATH='.'

# 单测：当前基线 failures=4, errors=6，不许更差
& .\app\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'

# 端到端：140 节点，0 断头、全部 done
& .\app\.venv\Scripts\python.exe _qa\_test_skeleton_e2e.py 140

# 模型路径（假 provider 模拟模型行为）
& .\app\.venv\Scripts\python.exe _qa\_test_outline_model_path.py

# ref 重复
& .\app\.venv\Scripts\python.exe _qa\_test_dup_ref.py

# 出口去向标错
& .\app\.venv\Scripts\python.exe _qa\_test_exit_kind_mismatch.py

# skill 与工具白名单一致性
& .\app\.venv\Scripts\python.exe tools\_audit_skills.py
```

**改完先 `ast.parse` 或跑单测，别只看到"编辑成功"就交** ——
前面加注释忘对齐缩进，直接 `IndentationError`，整轮任务在 `jobs.generate` 就挂了。

### 2.5 不许动的东西

- `app/contract.py` 的**必填字段**
- `ui/` 下的任何前端文件
- `tests/` 里现有用例的**语义**（可以更新期望值，但不许删测试）

---

## 3. 交付要求

1. 改完的 `.py` 文件（**完整内容**，不要 diff 片段）
2. 新增/修改的测试，必须包含：
   - 模板生成的结构断言（唯一 start / 可达 / 0 断头 / 无环 / 结局数 / flavor 汇合）
   - 模型乱填（少给 / 多给 / 重复 / 塞结构字段 / 批次失败）时仍能出合法骨架
   - **180 节点、9 批、七种病全叠上**的综合用例
   - Part B 的「阶段契约」：某阶段降级后，下游能正确感知
3. `说明.md`：改了什么、为什么、怎么跑测试、B5 里那几个问题的答案

---

## 4. 一句话

> **模型擅长写剧情、写台词、起标题；它不擅长维护一张 DAG 的拓扑一致性 ——
> 这不是它笨，是这件事本来就不该交给它。**
>
> agent 之间也一样：**别让「靠 dict 传递、靠时序巧合」当契约**，
> 每个阶段产出什么、下游怎么判断能不能用，都该写成代码里能查的东西。
