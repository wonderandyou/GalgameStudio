# 任务：重做 GalgameStudio 的节点生成系统

> 这是给 **Grok** 的执行规格。写法按 xAI 官方 Best Practices：**任务 / 约束 / 输出格式前置**，
> 证据压成断言表，规格写成可执行断言，不写劝服性叙事。
> 原 GPT 版保留在 `提示词-重做节点生成系统.md`，两份可对比效果。

---

## 0. 直接开始

### 0.1 任务

把 `D:\软件\GalgameStudio` 的**节点图生成链路**重做。
范围：节点从哪来 → 怎么存 → 怎么校验 → 怎么播。

现状：能生成、能导出、能播，但**图的结构是坏的**，播放器一屏糊出十几个选项按钮。

### 0.2 三条硬约束

| # | 约束 |
|---|---|
| C1 | **换模型，不是打补丁。** 不接受在现有校验器上继续加规则 |
| C2 | **不扩大范围。** 不做 Unity 导出、不做联机、不做多结局解锁、不做条件分支的图拓扑 |
| C3 | **不做的事要说出来。** 某条你认为代价过大，直接说明理由，不要静默跳过 |

> **执行前提**：这个任务要改 `D:\软件\GalgameStudio` 里的真实文件、并跑 `unittest`。
> 需要能读写文件系统、能执行命令的环境（agent / CLI 模式）。
> 如果只是在纯聊天窗口里，你只能产出规格和代码骨架 —— 那种情况下**在开头说明这一点**，不要假装改过文件。

### 0.3 你要的输出格式

**分两轮交，不要一次给完。**

**第一轮 —— 计划（先交这个，我确认后再动代码）**

```
1. 你理解的问题根因（4 条以内，每条一行）
2. 你要改的文件清单，每个文件一句话说改什么
3. 三个 commit 的切分（A / B / C）和各自的验收命令
4. 你认为做不到或有风险的条目
```

**第二轮 —— 实现**

按 A / B / C 三个 commit 交。每个 commit 附：
`改了什么` / `为什么` / `验证命令 + 真实输出`。

**交付说明里禁止出现「应该没问题」「理论上可以」这类措辞。没跑过就说没跑过。**

---

## 1. 事实基线

下面每个数字都是我在真机真项目跑出来的。**当输入用，不要为了"确认"再重跑一遍。**

唯一例外：**如果磁盘上的实际数据和下表不一致，以磁盘为准，并在交付说明里指出这一行**——
那说明状态已经变了，我的表过期了。

### 1.1 出问题的项目

```
projects\p_04927612e8c44ec0b5519223a2a464e5\graph.json
```

| 指标 | 实测值 |
|---|---|
| 节点总数 | 128（scene 93 / choice 30 / ending 4 / start 1） |
| `edges` 条数 | **640** |
| 唯一 `(from,port,to)` | **114** |
| 冗余边 | **526（82%）** |
| `choices` 长度分布 | `{2: 19 个, 4: 1 个, 26: 10 个}` |
| `n1`（start）出边数 | **27**（应 1） |
| edge id 构成 | 后端格式 `e_nN_i` **52 条** + 前端格式 `e数字` **588 条** |

### 1.2 从数字直接读出的四件事

1. `588 = 14 × 42` → **前端保存了 14 次，每次把本章 42 条边追加一遍**。这张图原本只有 52 条边。
2. `26 = 2 × 13` → 那 10 个 choice 节点的选项数组也被追加了 13 次，选项内容完全重复。
3. `start` 27 条出边、`choice` 26 个选项，**都通过了保存校验并落盘** → 保存路径没跑语义校验。
4. 播放器渲染 27 个按钮（960px 容器里露出 12 个）→ 播放器读的是 `links`，不是 `nodes[].choices`。

### 1.3 四条根因（代码位置）

| 根因 | 位置 |
|---|---|
| **双真相**：`graph['edges']` 与 `nodes[].choices` 都存连接，无强制投影；两边 id 规则还不一样 | `app/engine/graph.py:56`（`e_{nid}_{i}`）vs `ui/app.js:3932`（`e{i+1}`） |
| **前端合并无去重**：过滤一端在章外的旧边后直接 concat | `ui/app.js:912` |
| **保存接口只跑 pydantic**：`Graph.model_validate(body)` 之后直接落盘，没调 `validate_graph` | `app/mock_api.py:934` |
| **生成器形状是树**：每个分支长自己的子树，叶子必然断头 | `app/agents/outline.py::build_template()` |

补充：`validate_graph` 里的边一致性检查在 `graph.py:103`，`分支须 2–4 个` 在 `tools.py:1457` —— **两个校验器都存在，但都装在保存路径之外。**

### 1.4 一个独立漏点

画布占位标签漏进产物：`ui/graph-editor.js:836` 写死 `['选项 A','选项 B','选项 C','选项 D']`，
`ui/app.js:1431` 写死 `选项 A/B`。保存时未过滤未填写的占位项 → 玩家看到字面量「选项 C」。

---

## 2. 目标架构：三条设计决定

### D1 —— `edges` 降级为派生投影

**`nodes` 是唯一真相，`edges` 永远重算。**

- 落盘时 `edges` 可写可不写；**读回来一律忽略磁盘值**，用 `edges_from_nodes(nodes)` 重算
- **客户端传来的 `edges` 直接丢弃**，不参与合并 / 比较 / 校验
- 任何返给客户端的图，`edges` 都是刚算的那份

```python
def normalize_graph(graph):
    nodes = graph.get('nodes') or []
    out = {k: v for k, v in graph.items() if k != 'edges'}
    out['edges'] = edges_from_nodes(nodes)
    return out
```

> 这条落地后，根因 1 和 2 在数学上不再可能发生 —— 重复的边没有地方寄存。

### D2 —— 生成器改成「主干 + 会汇合的侧枝」

真实 galgame 的分支是 **branch & recombine**：分出去 → 走一小段 → 回到主干。
树形生长必然产生断头；汇合形生长不可能产生断头。

新增硬不变量 `rejoin_max`，见 §3.2 / §3.3。

### D3 —— 单一写入门

所有写入路径（API / agent / checkpoint 恢复 / 导入）过同一个函数，无任何 `source` 可跳过。

---

## 3. 规格：节点模型

### 3.1 字段

**Node**

```jsonc
{
  "id": "s001",                  // 全局唯一；格式见下
  "type": "scene",               // scene | choice | ending（start 见 3.1.1）
  "title": "深夜的站台",
  "summary": "一句话梗概",
  "lines": [{ "who": "她", "text": "…", "voice": null, "dur": 3.2 }],
  "bg": { "asset": "…", "prompt": "…", "seed": 0, "provider": "…" },
  "chars": [{ "id": "heroine", "pos": "center", "stance": "stand" }],
  "choices": [ /* choice 节点才有 */ ],
  "end_kind": "true",            // ending 节点才有：true | good | bad | alt
  "pos": { "x": 0, "y": 0 }
}
```

**choices[]（choice 节点）**

```jsonc
{
  "label": "看清日期",
  "to": "s012",
  "effects": { "knows_date": true },   // 旗标写入；cosmetic 选项必须有
  "condition": null                    // 可选：读旗标决定是否显示
}
```

**choice 节点新增 `kind`**：`"key"`（会改拓扑）/ `"cosmetic"`（只改旗标）。

**graph 顶层新增 `flags`**

```jsonc
"flags": {
  "knows_date": { "type": "bool", "default": false },
  "affection":  { "type": "int",  "default": 0 }
}
```

**本阶段旗标只要求两点**：`effects` 能被存下并被播放器应用；`condition` 能读旗标决定选项是否显示。
**条件分支的图拓扑明确排除**（`if` 导致跳不同节点 —— 不做）。

#### 3.1.1 关于 `start` 类型

现在 `start` 是独立类型且承担「必须唯一 + 恰好一个出口」双重语义，在合并逻辑里被特殊对待了十几处，是 bug 温床（证据：它长出 27 条出边没人拦）。

**推荐**：去掉 `start` 类型，起始就是第一个 `scene`（`s001`）。
**允许保留**，但必须满足同样的出边数不变量，且不得在合并逻辑里开特例。

#### 3.1.2 节点 id 规则与迁移（**这条别搞错，会砸掉所有旧项目**）

**硬要求只有一条：全局唯一。** 格式是第二位的。

推荐新项目用：`^[sc]\d{3}$|^e\d{3}$`（`s`=scene / `c`=choice / `e`=ending）。
id 由**生成器分配**，不让模型指定。用一个 `next_id` 计数器，从当前最大值 +1 开始，
**永不回退、永不复用** —— 复用 id 会让旧 checkpoint 指错节点。

⚠️ **迁移警告**：磁盘上现存的所有项目用的都是 `n1 … n128`。

| 场景 | id 规则 |
|---|---|
| 新建项目 | 用 `s001` 格式 |
| 读旧项目 | **只校验唯一性，不校验格式**，`n1` 原样保留 |
| 旧项目改 id | **不要做。** id 变了图就变了，`checkpoint`、`agent_state.nodes`、导出产物全部对不上 |

如果加一条「id 必须是 `s001` 格式否则拒绝」，**所有旧项目会全部打不开。**

顺手建议：消灭 `ref` / `id` 双轨制。现在模型写 `ref`、代码再映射成 `id`（`by_ref` / `by_id` 两套索引），
历史上的 `骨架节点 ref 重复：k161` 就是从这漏出来的。让模型完全不碰 id/ref 最干净。

### 3.2 不变量（全部可执行）

| # | 不变量 |
|---|---|
| I1 | `edges == edges_from_nodes(nodes)`，恒等 |
| I2 | 节点 `id` 全局唯一 |
| I3 | 出边数：`scene` = 1，`choice` = 2 或 3（关键点允许 4），`ending` = 0 |
| I4 | choice 的 `port` 连续 `0..n-1`，无空洞 |
| I5 | 同一 `(from, port)` 最多一条边 |
| I6 | 从起始节点 DFS 可达全部节点 |
| I7 | 无环（拓扑排序成功） |
| I8 | ≥1 个 `ending`，且 ≥1 个 `end_kind == "true"` |
| I9 | `choice_count / node_count <= choice_density * 1.5` |
| I10 | 分叉深度 ≤ `depth_max` |
| I11 | **每条侧枝在 `rejoin_max` 步内汇合到入度 ≥2 的节点，或到达 ending** |

**I5 的键是 `(from, port)`，不是 `(from, to)`。**
多个选项指向同一节点是合法的（cosmetic choice），此时是两条 port 不同的边。

> 现有 `tools.py:1460` 的 `len({c['to'] for c in choices}) != len(choices)` 与 I5 冲突，**删掉它**。

### 3.3 汇合检查算法

```python
def check_rejoin(nodes, choice_node, rejoin_max=10):
    """返回问题列表；空列表 = 通过。"""
    by_id = {n['id']: n for n in nodes}
    indeg = Counter(e['to'] for e in edges_from_nodes(nodes))
    problems = []
    for c in choice_node['choices']:
        cur, steps = c['to'], 0
        while steps < rejoin_max:
            n = by_id.get(cur)
            if n is None:
                problems.append(f'{c["label"]} 指向不存在的 {cur}')
                break
            if n['type'] == 'ending':
                break                              # 走到结局：合法
            if indeg[cur] >= 2 and steps > 0:
                break                              # 已汇合：合法
            outs = n.get('choices') or []
            if len(outs) != 1:
                problems.append(f'{c["label"]} 在 {cur} 处再度分叉，超出范围')
                break
            cur = outs[0]['to']
            steps += 1
        else:
            problems.append(f'{c["label"]} 走 {rejoin_max} 步仍未汇合')
    return problems
```

---

## 4. 规格：生成器

### 4.1 分工不变

`build_template()` 用纯代码定死拓扑，模型只填 `title` / `summary` / `choices[].label` / `lines`。

**核心不变量测试必须保留并加强：**

```python
assert topology(skeleton) == topology(template)
```

### 4.2 形状

```
s001 ─ s002 ─ s003 ─ c004 ─┬─ s005 ─ s006 ─┐
                            │                ├─ s010 ─ s011 ─ c012 ── …
                            └─ s007 ─ s008 ─┘         │
                                                      ├─ s013 ─ s014 ─┐
                                                      │                ├─ s020 ─ …
                                                      └─ s015 ─ s016 ─┘
```

### 4.3 参数（可配，给默认值）

| 参数 | 默认 | 含义 |
|---|---|---|
| `total_nodes` | 180 | 目标节点总数 |
| `chapters` | 9 | 章数 |
| `choice_density` | 0.06 | 选择点 / 总节点（≈每 17 个一个） |
| `branch_len` | 3~6 | 每条侧枝走几个节点后汇合 |
| `rejoin_max` | 10 | 侧枝必须在这么多节点内汇合 |
| `depth_max` | 2 | 最多嵌套几次分叉 |
| `endings` | 4 | 1 true + 2 good/bad + 1 alt |

### 4.4 先算数，再建图

```
选择点数   = round(total_nodes × choice_density)   # 180 × 0.06 ≈ 11
侧枝节点数 ≈ 选择点数 × (branch_len - 1)           # 11 × 4 ≈ 44
主干节点数 = total_nodes - 侧枝节点数
```

现有代码是「建完再数够不够」，所以出现 `骨架节点数 150 超出目标 100 的 ±10%` 这种要重试的失败。
**改成先算后建，这个失败类别直接消失。**

### 4.5 结局挂载点

结局只从**主干末尾**和 **`kind='key'` 选择的末端**长出，不从侧枝中段长出。
保证：侧枝总能汇合回主干；结局数量 = 主干收束数，可控。

---

## 5. 规格：写入门

### 5.1 唯一入口

```python
def apply_graph(graph, *, source):
    graph = normalize_graph(graph)          # edges 重算
    problems = validate_graph(graph)        # 返回问题列表，不抛
    if problems:
        raise GraphRejected(source, problems)
    return graph
```

`source` 仅用于日志（`api.save` / `agent.writer` / `checkpoint.restore`）。
**任何 source 都不跳过校验。** 要放宽就改校验器本身。

### 5.2 保存接口

`app/mock_api.py` 的 `save_graph`：

```python
Graph.model_validate(body)              # 保留：字段类型
apply_graph(body, source='api.save')    # 新增：语义不变量
```

422 时**列出全部 problems**，不只第一条 —— 让前端一次看到「start 27 条出边 / c007 26 个选项 / 5 条重复边」。

#### 5.2.1 存量脏图必须分级（**实测数据支持，很关键**）

保存路径统一严格会锁死主人的真实项目。全量实测（593 个项目）：

| 结果 | 数量 | 占比 |
|---|---|---|
| 能过 I1–I8 | 562 | 95% |
| **会被拒** | **31** | **5%** |

被拒的正好是主人在用的那几个：

| 项目 | 节点 | 首个问题 |
|---|---|---|
| `p_b097ebf4…` | 510 | `n38(scene) 出边=0` |
| `p_04927612…`（出问题的那个） | 128 | `n44(choice) 出边=26` |
| `p_ff87c89d…` | 96 | `n15(scene) 出边=0` |
| `p_38052ad6…`（「5」） | 88 | `n13(scene) 出边=0` |

**规则：按「图的新旧」分级，不按「检查项」分级。**

| 条件 | 行为 |
|---|---|
| `agent_state.skeleton` **存在**（新流程产物） | 走完整 `apply_graph`，**严格拒绝** |
| 没有 `skeleton`（存量旧图） | 只 `normalize_graph` 重算 `edges` + 记 warning，**不拒绝** |

同理 `api.regenerate`（单节点重生成）对旧图只 normalize 不拒绝 ——
一个节点的台词重写，不该因为这张历史图有 32 处断头就整个 422。

> **能保存 ≠ 能玩。** 分级只保证「数据不丢、不锁死」，不保证旧图能跑通。
> 旧图该有的断头还在，视频应当用新生成的图。

**还要实测验证一条**：`pipeline.save` 也接了这个门，而磁盘上**带 `skeleton` 的项目里就有断头的**
（`p_38052…` 的 `n13`）。生成链路必须真跑一遍，确认新流程不会再产出断头 ——
否则生成到一半就 422。

---

### 5.3 前端

1. **`ui/app.js:3931` 的 `edges: links.map(...)` 删除**，请求体不带 `edges`。
2. **`ui/app.js:912` 章节合并改为按 `(from, port)` 去重**：

```js
const seen = new Set();
links = links.concat(d.links).filter((l) => {
  const k = l.from + '#' + (l.port || 0);
  if (seen.has(k)) return false;
  seen.add(k);
  return true;
});
```

3. **占位标签过滤**：`buildGraphPayload()` 里丢掉 `label` 命中 `^选项 [A-D]$` 且 `to` 为空的项。
   更稳的做法是给占位项加 `placeholder: true` 标记，按标记丢，不靠文字匹配。

### 5.4 播放器

`choiceTargets()` **只读 `n.choices`**：

```js
function choiceTargets(n) {
  return (n.choices || []).map((c, i) => ({
    label: c.label || ('选项 ' + String.fromCharCode(65 + i)),
    to: c.to
  }));
}
```

加脏数据兜底：

```js
if (targets.length > 4) {
  console.error('[player] 选项数异常', n.id, targets.length, targets);
  targets = targets.slice(0, 4);
  toast('这张图的数据有问题，已临时只显示前 4 个选项（详见控制台）');
}
```

---

## 6. 禁止实现的方案

这三种都实际试过并回滚了。**实现前先确认你要做的不是它们的变体。**

| 方案 | 为什么不行 |
|---|---|
| **事后结构修复**：图长完发现断头，自动给断头节点补边（`_fix_dangling`） | 补的边让被补节点变成不可达；再修不可达必须改起始节点唯一出口 → 级联破坏。两个函数已删除 |
| **用硬校验拦模型的主观猜测**：给模型加节点数 ±10%、分支深度、出口结局标签一致性等约束 | 这些字段本来就是模型猜的，每加一条约束就多一个必失败场景，最后死循环在「改 A 坏 B」 |
| **靠重试掩盖系统性错误**：`ref` 重复就重试 | 重试三次拿到 `k081/k061/k161`，仍然重复。重试只对随机性有效 |

**通用原则**：模型猜不准的字段就不要让它猜，由代码算。
出口的结局类型、node id、port 编号、边 —— 全部代码定，模型只填文字。
（`build_template()` 现在已经部分做到了，保持这个方向。）

---

## 7. 验收规格

每条都要有真实命令输出。没输出等于没做。

### 7.1 `tests/test_graph_invariants.py`（新建）

| 测试 | 断言 |
|---|---|
| `test_edges_always_projection` | 任意含冗余边的图经 `normalize_graph` 后 `edges == edges_from_nodes(nodes)` |
| `test_client_edges_ignored` | 27 条出边的 start 图提交保存接口 → **422**，错误信息含 `出边` |
| `test_oversized_choices_rejected` | 26 个选项的 choice → **422** |
| `test_cosmetic_choice_allowed` | 两选项指向同一节点 → **通过** |
| `test_duplicate_port_rejected` | 同一 `(from,port)` 两条边 → 拒绝 |
| `test_every_branch_rejoins` | 生成模板中每条侧枝在 `rejoin_max` 内汇合 |
| `test_zero_dead_ends` | 非 ending 节点出边数 ∈ {1} ∪ [2,4]，**没有 0** |
| `test_choice_density` | `choice_count / node_count <= choice_density * 1.5` |
| `test_start_exactly_one_exit` | 起始节点恰好 1 条出边 |
| `test_no_cycles` | 拓扑排序成功 |
| `test_all_reachable` | 从起始节点 DFS 覆盖全部节点 |

### 7.2 规模与容错

保留并加强现有注入测试：

```
test_180_nodes_nine_batches_survive_seven_model_faults
```

七类注入：重复 ref / 自环 / 断头 / 错结局标签 / 深度超限 / 少给节点 / 截断 JSON。
断言：**无论注入什么，`topology(skeleton) == topology(template)` 恒成立。**

### 7.3 端到端

```
python _qa\_test_skeleton_e2e.py 180
```

期望：`完成度 180/180`、`断头节点 0 个`、`全部通过`。

### 7.4 播放器（人工确认，附截图）

- [ ] 一个选择点最多 4 个按钮
- [ ] 无重复选项文字
- [ ] 每个选项都能走到下一节点，不出现「这个节点后面没接线了」
- [ ] 走完整条主干不卡死路
- [ ] 控制台无 `[player] 选项数异常`

### 7.5 回归基线

```
python -m unittest discover -s tests -t .
```

当前基线：**94 项 / 3 失败 / 6 错误**（部分是历史遗留）。
要求：失败数不增加。修掉的项目单独列出。

---

## 8. 参考：真实 galgame 怎么做的

依据 Ren'Py 官方文档（业界事实标准）：

- [Quickstart](https://www.renpy.org/doc/html/quickstart.html)：`label` / `menu` / `jump` / `default`+`$`+`if` / `return` 六件套
- [Branching & Recombining](https://sources.debian.org/data/main/r/renpy/6.10.2.dfsg1-1/doc/tutorials/Branching_%26_Recombining_the_Story.html)：**分支走完要 `jump` 回主干**
- [Remembering User Choices](https://sources.debian.org/data/main/r/renpy/6.6.2.dfsg1-1/doc/tutorials/Remembering_User_Choices.html)：选项后果存旗标，多数不改拓扑

三条可迁移的结论：

1. **分支必须汇合** —— 这是 I11 的来源，不是风格偏好。
2. **多数选项只改旗标不改拓扑** —— 所以 cosmetic choice 是合法的，别拦。
3. **label 全局唯一** —— 所以节点 id 唯一性是硬要求，不是洁癖。

真实 galgame 指标参照：约每 15~40 个节点一个选择点。当前是每 4.3 个，高 4~10 倍。

---

## 9. 文件清单

```
app/engine/graph.py          edges_from_nodes / validate_graph / public_graph / normalize_graph(新)
app/engine/skeleton.py       SkeletonNode / to_graph_stub / validate_skeleton
app/engine/checkpoint.py     checkpoint 里的 nodes/edges 计数
app/agents/outline.py        build_template / generate_skeleton / 分批填槽
app/agents/fill.py           只填台词
app/agents/tools.py          AgentTools / _commit / write_nodes / 各类校验（含删掉 1460 那条）
app/agents/loop.py           writer 循环 / FILL_ROUNDS / 章节作用域
app/contracts.py             Choice / Node / Graph pydantic 契约
app/mock_api.py              save_graph（PUT /projects/{id}/graph）
ui/app.js                    choiceTargets / buildGraphPayload / syncFromEditor
ui/graph-editor.js           画布出口数与 choiceLabels
tests/                       test_outline_template.py / test_stage_contracts.py / test_skeleton.py
                             test_graph_invariants.py（新建）
```

---

## 10. 一句话设计判断

> 真实 galgame 的分支是「出门走一小段，然后回家」，不是「一直往下长」。
> 生成器一直在往下长，所以必然长成一棵会断头的树。
> 改成「一定会回家」，断头就从「要修」变成「不可能发生」。
