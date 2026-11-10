# 交给 Astra 的后端任务：让内置 Agent 真的能写上千个节点

> 用法：把下面「==== 复制开始 ====」之后的内容整段发给 Astra。
> 这份提示词是自包含的 —— 对方看不到我们的对话，所以路径、现状、接口都写在里面。

==== 复制开始 ====

你要接手一个已有的 Python 后端项目的一部分工作。**先读代码再动手**，不要凭我的描述猜结构。

## 项目

路径：`D:\软件\GalgameStudio`

这是一个 Galgame（视觉小说）制作器。玩家输入一句需求，程序内部有 4 个 Agent
（编剧 writer / 美术 art / 配音 voice / 装配 build）协作，生成可玩的游戏并导出。

- 后端：Python 3.11 + FastAPI，代码在 `app/`
- 解释器：`D:\软件\GalgameStudio\app\.venv\Scripts\python.exe`
- **必须在 `D:\软件\GalgameStudio` 目录下运行**（app 是顶层包）
- 数据契约：`app/contract.py`（pydantic，`extra='forbid'`，改字段会连锁校验失败）
- 图引擎：`app/engine/graph.py`、交互流水线：`app/engine/pipeline.py`
- API 层：`app/mock_api.py`（名字叫 mock，但它是唯一的 API 实现，用 `GAL_MOCK` 环境变量切换真实/mock 数据）

## 现状（这是你要改的）

**问题一：一次生成全部，节点数上不去**

`app/engine/pipeline.py:147` 的 `write_script()` 把整张图的所有节点一次性丢给 LLM，
让它返回一个大 JSON。20 个节点还行，但：

- 节点一多，输出会被长度截断，JSON 不完整 → 解析失败 → 整个阶段降级成模板
- 上下文越滚越长，越往后写得越差
- 一旦中途失败，前面的全丢

**问题二：参数硬限制**

`app/mock_api.py:40`：`node_count: int = Field(default=8, ge=4, le=64)`。
上限只有 64。而一个 1~2 小时的 Galgame 是 **700~1000 个节点**。

**问题三：没有真正的 Agent 循环**

现在没有「LLM 调用工具、工具改图、结果回灌给 LLM」的循环，
`pipeline.py` 是线性的：调一次 LLM → 套用结果 → 结束。

## 已经定好的接口（别改，按这个实现）

有人已经写好两个文件，它们是本任务的**输入契约**：

### 1. `app/agents/tool_schema.py`

定义了 4 个 Agent 各自可用的工具（共 20 个），OpenAI function-calling 格式。

关键约定（**必须遵守**）：

| 约定 | 说明 |
|---|---|
| `MAX_NODES_PER_CALL = 20` | 一次 `write_nodes` 最多 20 个节点，这是长篇不崩的核心 |
| 参数比契约简单 | 工具里台词是 `lines: ["台词1","台词2"]`（字符串数组），**不是**契约里的 `[{who,text,voice,dur}]`。`dur` 由你按字长算，`who` 缺省继承节点级 `who` |
| `ref` 而不是 id | 写节点时用章内临时键（如 `c03-s04`），连接/分支都引用 `ref`，正式 id 由后端分配 |
| 起点节点自动建 | 编剧不写 `type=start` 的节点，由系统创建 |

### 2. `app/agents/registry.py`

把每个 Agent 的「身份 / skill 文档目录 / 工具白名单 / 红线」绑在一起。
skill 正文在 `app/skills/<agent>/SKILL.md`（已写好，是给模型看的工作手册，**不要改内容**）。

`registry.system_prompt(agent)` 已经能拼出完整的系统提示 = 身份 + 红线 + 工具清单 + skill 全文。

先跑一次自检确认现状：

```powershell
cd D:\软件\GalgameStudio
.\app\.venv\Scripts\python.exe -m app.agents.registry
```

应当输出 `✓ 4 个 agent，20 个工具，skill 齐全`。

## 你的任务

### 任务 1：给 LLM provider 加 tool calling 能力（前置）

`app/providers/llm.py` 现在的 `call()` 只返回 `str`，不支持 tools。

需要新增一个方法（或改造 `call`），要求：

- 请求体支持标准的 OpenAI `tools` / `tool_choice` 字段
- 响应能取回 `choices[0].message.tool_calls`（列表，每项含 `id` / `function.name` / `function.arguments` 的 JSON 字符串）
- 同时能取回普通文本
- 保持现有 `call()` / `call_json()` 的行为不变（其它地方在用），**只做加法**
- 参数不合法、响应结构异常时抛 `ProviderError`（照 `app/providers/base.py` 里的写法），不要静默降级

建议签名：

```python
async def call_with_tools(self, messages, tools, temperature=None, max_tokens=None, timeout=180):
    """返回 dict：{'text': str|None, 'tool_calls': [{'id','name','arguments'(dict)}]}"""
```

### 任务 2：实现 20 个工具的实际动作（`app/agents/tools.py`）

新建文件。每个工具是**同步或异步函数**，接收解析后的参数 dict，返回 JSON 可序列化的结果 dict。

实现时遵守：

- **落盘必须立刻**：`write_nodes` 一返回，节点就已经写进 `graph.json`（复用现成的原子写工具，别自己造）
- **失败返回结构化错误**，不要抛裸异常给 LLM：`{'ok': False, 'error': 'CODE', 'message': '人话', 'hint': '怎么改'}`
  —— LLM 是靠这个 hint 自我纠正的，写清楚
- **`validate_graph` 要真的查**：断边、孤儿节点、死路、choice 出口不足、ending 缺 `end_kind`、`to` 指向不存在
- **`write_nodes` 要展开完整契约**：把简化的入参变成 `contract.Node` 要求的完整结构，
  补齐 `status='pending'`、`bg`（用 `bg_key` 推 prompt）、`pos`（自动布局，别全堆在 0,0）、`gen`、`lines[].dur`
- **`bg_key` 要保存**：它是美术阶段归并生图的依据，写到节点的 `properties` 或单独的索引里都行，但要能被 `list_pending_assets` / `generate_image` 读到

### 任务 3：加「章节」这一层（结构）

现在图是平铺的节点列表。要支持千节点，必须有分卷结构：

```
项目 → 幕 act（3~6） → 章 chapter（每幕 4~8 章） → 节点（每章 12~16）
```

要求：

- 章节信息落盘（如 `project/chapters.json` 或 graph.json 里独立一段），含：`id`、`title`、`beats`、`node_budget`、`status(pending|writing|done)`、`summary`
- 节点要能追溯到所属章（`chapter_id`）
- **`contract.py` 的 Node 字段一个都不能改**（`extra='forbid'` 会连锁失败）。
  章节归属放节点之外的索引里，或者在契约里新增**可选**字段并同步改 `engine/graph.py` 的序列化/反序列化
- `finish_chapter` 写入的 `summary`，必须能在 `get_chapter` 里作为 `prev_summary` 给后续章节看到

### 任务 4：写 Agent 循环（`app/agents/loop.py`）

核心。伪代码：

```
while 还有未完成的章:
    chapter = 取下一个 pending/writing 章
    messages = [system(registry.system_prompt('writer')), user(本章任务)]
    while 本章节点数 < 目标 and 未超过单章最大轮数:
        resp = llm.call_with_tools(messages, tools)
        if resp.tool_calls:
            for call in resp.tool_calls:
                result = dispatch(call)          # 调 tools.py
                messages.append(tool 结果)        # 把结果回灌
        else:
            break                                # 模型不再调工具 = 本章写完
    校验 → 失败就让模型重试（最多 2 次）→ finish_chapter
```

必须做到：

- **断点续跑**：任何一步中断，重来时跳过已 done 的章，从 `status=writing` 的章接着写
- **单轮上限**：防止模型死循环刷工具（建议每章最多 40 次工具调用）
- **失败不静默**：LLM 报错要让 job 状态可见，不要假装写完
- **进度上报**：复用 `pipeline.py` 里现成的 `self.agent(...)` / `self.node(...)` / `self.log(...)` 事件通道，前端靠它显示进度

### 任务 5：放开参数上限

`app/mock_api.py` 的 `Params`：

- `node_count` 上限从 64 放开到 **2000**
- 增加 `target_minutes`（目标游玩时长，分钟，默认 60）
- 保留 `branch_depth` / `auto_voice` / `consistency` / `resolution`
- 前端可能不传新字段 —— 给默认值，别让已有请求失败

## 验收标准（必须自己跑通，不许只说「应该可以」）

1. `python -m app.agents.registry` 通过
2. 用 mock 模式跑一个**至少 300 节点**的生成任务，全程不崩，落盘正确：
   ```
   $env:GAL_MOCK=1; $env:GAL_PORT='8790'
   ```
   起服务后用 `POST /api/projects/{id}/generate` 触发，观察 job 事件流
3. 生成完 `validate_graph(scope=all)` 零 error
4. 中途 kill 服务再重启，能接着写（不是从头来）
5. 明确报告：跑了多少节点、多少章、多少轮工具调用、用了多久

## 不要做的事

- **不要改 `app/skills/` 下的任何文件**（那是给模型的工作手册，内容已定稿）
- **不要改 `contract.py` 里已有字段的名字或必填性**
- **不要引入需要联网安装的新依赖**（本机禁装包，只用标准库 + 已有依赖）
- **不要动 `ui/` 前端**
- 不要在关键路径上写 `except: pass`

## 交付

做完给我：

1. 改了哪些文件（路径 + 一句话说明）
2. 验收 5 项各自的实测结果（贴关键输出，不要只说「通过」）
3. 已知限制 / 没做完的部分

有拿不准的地方，先说清楚卡在哪、需要什么信息，不要猜着写完。

==== 复制结束 ====
