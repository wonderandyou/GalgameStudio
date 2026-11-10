# 内置 Agent 的 skill 与工具

这个目录是**程序内置 Agent 的工作手册**，不是给人看的说明文档。

每个子目录对应 `app/agents/registry.py` 里的一个 agent，入口是 `SKILL.md`，
其余分篇按 `01-` `02-` 前缀排序，会按顺序拼进系统提示。

---

## 结构

```
app/
├─ agents/
│  ├─ registry.py       agent 定义：身份 / skill 目录 / 工具白名单 / 红线
│  └─ tool_schema.py    工具集（OpenAI function-calling 格式）
└─ skills/
   ├─ writer/           编剧
   │  ├─ SKILL.md       入口：红线、时长换算、作业循环、工具速查
   │  ├─ 01-longform.md 千节点分批作业规程
   │  ├─ 02-dialogue.md 台词的味道
   │  ├─ 03-branch.md   分支与结局
   │  └─ 04-continuity.md 摘要/伏笔/角色一致
   ├─ art/              美术（7 篇）
   │  ├─ SKILL.md          入口：红线、作业顺序、四类资产、完成标准
   │  ├─ 01-style-bible.md 风格圣经：全片唯一的视觉输入源
   │  ├─ 02-background.md  背景：bg_key 归并、地点×时间×天气、无人
   │  ├─ 03-character.md   立绘：外形锚点、差分、一致性
   │  ├─ 04-interface.md   界面：六块皮（启动/对话框/选项/存档/设置/顶栏）
   │  ├─ 05-typography.md  文字：四级字号、三档字体、可读性底线
   │  └─ 06-gate.md        验收关卡：什么算过关、什么必须重画
   ├─ voice/SKILL.md    配音：时长回写、音色统一
   └─ build/SKILL.md    装配：错误分级、素材检查、导出
```

## 四个 Agent 与流水线对应

| agent | 阶段 | 工具数 | 干什么 |
|---|---|---|---|
| `writer` | 编剧 | 11 | 生成章节结构、节点、台词、分支 |
| `art` | 美术 | 3 | 按 bg_key 批量出背景与立绘 |
| `voice` | 配音 | 3 | 逐句合成，回写真实时长 |
| `build` | 装配 | 3 | 结构校验、素材检查、导出 |

## 关键设计

**为什么必须有工具，而不是一次让 LLM 吐 JSON**

一个 1~2 小时的 Galgame 是 700~1000 个节点。任何「一次生成全部」的尝试都会
在中途被输出长度截断，而且错误会一路累积。

工具把这件事拆成小步：`write_nodes` 一次写 8~15 个节点，写完立刻落盘、
立刻校验。一千个节点 = 六七十次小调用，每一次都可控、可恢复、可重试。

**参数比契约简单**

契约里 `lines` 是 `[{who, text, voice, dur}]`，工具里只要 `["台词1", "台词2"]`。
`dur` 由后端按字长算，`who` 缺省继承节点级。少一层嵌套，模型少犯错、少烧 token。

**ref 而不是 id**

写节点时用章内临时键 `ref`（`c03-s04`），连接和分支都引用 ref，
正式 id 由后端分配。模型不必猜 id 会不会冲突。

## 自检

```powershell
cd D:\软件\GalgameStudio
.\app\.venv\Scripts\python.exe -m app.agents.registry
```

输出 `✓ 4 个 agent，20 个工具，skill 齐全` 才算配置正确。
它会检查：skill 文件是否存在、工具白名单与 schema 是否一致、每个工具是否有描述。

## 改 skill 的规矩

- skill 是**给模型看的操作手册**，写「做什么、按什么顺序、什么算完成」，不写背景介绍
- 每条规则要能被执行或被检查，不写「要有感情」这种没法验证的话
- 数字必须和后端代码一致（单句长度、单次写入上限、字数预算）
- 改完跑一次自检
