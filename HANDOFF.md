# 奇迹工坊 · 工程交接

> 写给下一个对话。读完这份就能接着干活，不用重新摸。
> 最后更新：2026-11-07（多 agent 并行第一波：上传 / 抠图 / 参考图生图 / Unity 导出接通）

---

## 0. 产品目标流程（主人 2026-11-07 亲口定义，**判断"打通没打通"的唯一标准**）

> 别自己另起一套标准。核功能时照这 9 条逐条过。

```
1. 创建新项目
2. 上传自己的角色立绘参考图 + 场景风格参考图（后面可改）
3. 输入自己的需求
4. 多个 agent 共用一个 API：生成文本 → 分开进入「对话」「旁白」
   agent 根据对话/旁白做背景 —— 要仿照参考图的画风；没有参考图就自由发挥
5. agent 根据文本生成人物（动作/神态/表情），扣好图后放素材库
6. agent 分节点，按内容判断「分支 / 普通 / 结局」，再把立绘、背景同步插进节点
7. 用户在编辑界面素材库上传语音，选语音后标注角色名；
   之后 agent 自动判断该用哪个语音，调 API 做 TTS 配音朗读
8. agent 生成界面风格（文本框 / 设置图标 / 跳过图标等）
9. 玩家点保存 → 自动保存为 Unity 引擎的 .exe
```

**关键判据（最容易糊过去的四条）**：
- 步骤 4：**共用一个 API**，不是每个 agent 一套 key
- 步骤 4：背景**必须仿参考图画风**，没参考图才自由发挥
- 步骤 5：立绘要**扣好图**（透明底），不是白底方图直接塞
- 步骤 9：**点一下保存就出 .exe**，不是"另外跑两个脚本"

---

## 0.1 三十秒上手

```powershell
# 解释器（唯一使用这个，别用系统 python）
D:\软件\GalgameStudio\app\.venv\Scripts\python.exe

# 起后端（会自动设 GAL_STATE_DIR，别手动起 uvicorn）
powershell -ExecutionPolicy Bypass -File D:\软件\GalgameStudio\scripts\run-backend.ps1

# 起桌面程序（pywebview 壳，自动拉起后端 + UI 静态服务）
D:\软件\GalgameStudio\launcher\galapp.py   ← 用上面那个解释器跑

# 一条命令验证整条链路
$env:PYTHONPATH='D:\软件\GalgameStudio'
& 'D:\软件\GalgameStudio\app\.venv\Scripts\python.exe' D:\软件\GalgameStudio\tools\_test_agent_real_path.py 40
```

---

## 1. 路径总表

| 是什么 | 路径 |
|---|---|
| **项目根** | `D:\软件\GalgameStudio` |
| Python 解释器 | `D:\软件\GalgameStudio\app\.venv\Scripts\python.exe`（3.11.9） |
| 后端代码 | `D:\软件\GalgameStudio\app\` |
| 数据契约 | `D:\软件\GalgameStudio\app\contract.py` |
| Agent 注册表 | `D:\软件\GalgameStudio\app\agents\registry.py` |
| Agent 工具实现 | `D:\软件\GalgameStudio\app\agents\tools.py`（约 1090 行） |
| 工具 schema | `D:\软件\GalgameStudio\app\agents\tool_schema.py` |
| **Agent skill 文档** | `D:\软件\GalgameStudio\app\skills\<agent>\` |
| 流水线 | `D:\软件\GalgameStudio\app\engine\pipeline.py` |
| 图校验 | `D:\软件\GalgameStudio\app\engine\graph.py` |
| providers | `D:\软件\GalgameStudio\app\providers\` |
| 前端（纯 HTML/JS，无构建） | `D:\软件\GalgameStudio\ui\` |
| LiteGraph 库（**禁止修改**） | `D:\软件\GalgameStudio\ui\vendor\litegraph.js`（0.7.18） |
| 前端接口层 | `D:\软件\GalgameStudio\ui\api.js`（`DEFAULT_BASE` 在 11 行） |
| 桌面壳（pywebview） | `D:\软件\GalgameStudio\launcher\galapp.py` |
| UI 静态服务 | `D:\软件\GalgameStudio\launcher\ui_server.py` |
| 启动脚本 | `D:\软件\GalgameStudio\scripts\`（run-backend / run-mock / start / demo） |
| 项目数据 | `D:\软件\GalgameStudio\projects\` |
| 素材库（跨项目共用） | `D:\软件\GalgameStudio\.state\` 及 `app/assets/` |
| 验收脚本 | `D:\软件\GalgameStudio\tools\`、`D:\软件\GalgameStudio\tests\` |
| Unity 工程 | `D:\UnityProject` |
| Unity 构建产物 | `D:\UnityProject\Build\奇迹工坊.exe` |
| Unity 已部署副本 | `C:\Users\q3153\Desktop_new\奇迹工坊\` |

---

## 2. 端口与服务

| 端口 | 服务 | 说明 |
|---|---|---|
| **8790** | 真后端（FastAPI） | `ui/api.js` 的 `DEFAULT_BASE` 定死这个 |
| **8791** | mock 后端 | `scripts/run-mock.ps1`，演示用 |
| **8799** | UI 静态服务 | `launcher/galapp.py` 的 `SERVE_PORT`，只服务 `ui/` |

---

## 3. 工程结构

```
D:\软件\GalgameStudio\
├─ app\                      后端
│  ├─ main.py                FastAPI 入口（含 CORS）
│  ├─ contract.py            ★ 数据契约，extra='forbid'，改它要跑 check_contract.py
│  ├─ config.py              ROOT 在 import 那一刻定死，必须先设环境变量再 import
│  ├─ store.py               项目读写
│  ├─ agents\
│  │  ├─ registry.py         agent 定义：身份 / skill 目录 / 工具白名单 / 红线
│  │  ├─ tool_schema.py      工具 schema（OpenAI function-calling 格式）
│  │  ├─ tools.py            AgentTools 类：dispatch 按名字路由到同名 async 方法
│  │  ├─ loop.py             writer 的 LLM 循环
│  │  └─ mock_dialogue.py    确定性台词生成
│  ├─ engine\
│  │  ├─ pipeline.py         ★ 流水线：writer → art → voice → build
│  │  └─ graph.py            validate_graph 结构校验
│  ├─ providers\             六类 provider（qwen 生图 / 本地生图 / 百炼 TTS / 本地 TTS / LLM）
│  ├─ ui_presets\            ★ 界面风格预设（4 套）+ 渲染器 render.py
│  ├─ skills\                ★ Agent 工作手册（给模型看的，不是给人看的）
│  │  ├─ writer\ 5 篇
│  │  ├─ art\    7 篇
│  │  ├─ voice\  1 篇
│  │  ├─ build\  1 篇
│  │  └─ ui\     4 篇（界面 Agent）
│  └─ .venv\                 唯一解释器
├─ ui\                       前端（双击 index.html 也能跑，file:// 下靠 CORS 放行）
├─ launcher\                 桌面壳
├─ tools\                    验收 / 排查脚本
├─ tests\                    单元与契约测试
├─ docs\                     早期设计文档 + 过程截图（screens\ 里 50+ 张）
└─ projects\                 生成出来的项目
```

---

## 4. 现在做到哪了

### 4.1 已经能跑通的

| 模块 | 状态 | 证据 |
|---|---|---|
| 数据契约 + 图校验 | ✅ | `tests/check_contract.py` 1098 组合 PASS |
| 后端 API + SSE | ✅ | `tests/test_m1.py` 6 tests OK |
| writer agent（真 LLM 循环） | ✅ 代码路径通 | `tools/_test_agent_real_path.py 40` 全 ✓，47 次工具调用 |
| art agent（skill + 工具） | ✅ 本轮刚做完 | 7 项验收全 PASS，见 §5 |
| 素材库（三层：角色底图 / 表情姿势 / 背景） | ✅ | 内容寻址 `assets/<sha 前20位>.png` |
| 背景复用（地点×时间×天气） | ✅ | `generate_image` 自动查库 |
| 前端 LiteGraph 节点图 | ✅ | 40 节点加载、点按钮跳节点 |
| pywebview 桌面壳 | ✅ | `launcher/galapp.py` |
| Unity 播放器（真全屏 2560×1600） | ✅ | `D:\UnityProject\Build\奇迹工坊.exe`（89 MB） |
| 导出 Ren'Py / Web | ✅ | `run_export` |
| **界面预设 4 套 + 界面 Agent** | ✅ | 27/27 用例 PASS，见 §5.7 |

### 4.2 做到一半 / 有条件

| 模块 | 卡在哪 |
|---|---|
| **真生图** | 没有 Qwen API key，一直走 mock。`GAL_MOCK=1` 出占位色块 |
| **真 TTS** | 没有百炼 key，语音全是静音占位 |
| **`stats.cost`** | 硬编码 0.0，没真算钱 |
| **Unity 播放器** | 能全屏跑 40 节点，但**没接素材库的图**，还是占位 |
| **前端素材库面板** | 没做 |

### 4.3 还没做

- 参照实测的 galgame（《饿殍：明末千里行》）把**对话框 / 立绘位置 / 选项 / 转场**抄进 Unity 播放器
- 界面预设还没接进 Unity 播放器和 Ren'Py 导出（现在只出 HTML）
- `check_style` 只看元数据，**不看画面像素** —— 真正的"这张图颜色对不对"没有实现

---

## 5. 最近两轮改动（重点看 §5.7）

### 5.7 本轮：界面预设 4 套 + 界面 Agent

#### 5.7.1 为什么做预设，而不是「再画几套界面」

主人挑了四套启动界面排版后，需求是「做成预设界面风格 + 加一个 agent 按关键词做界面」。
关键在于：**界面最容易崩的不是做不出来，而是每做一个界面就换一套视觉语言** ——
启动页水墨、对话框亮蓝圆角、存档页暗色毛玻璃，玩家一眼看出是拼的。

所以界面和美术走同一条路：**先选预设，再渲染**，agent 不许从零发挥。

#### 5.7.2 四套预设（`app/ui_presets/*.json`）

| id | 名字 | 气质 | 菜单 |
|---|---|---|---|
| `ink-classic` | 水墨直系 | 宣纸留白、毛笔竖排、红色印章 | 竖排 |
| `cinema-dark` | 暗色电影感 | 全幅剧照压暗、左下大字、金色细线 | 横排 |
| `paper-indie` | 明亮 indie | 米白左文、右侧斜切配图、编号菜单 | 编号 |
| `theater-cold` | 冷色剧场 | 深蓝黑、细线框、居中细体大字距 | 竖排 |

每份预设 = 配色 + 字体 + 排版骨架 + **六块皮**（title/dialog/choice/save/settings/hud）的 token。
渲染器 `render.py` 按 id 分派 CSS 骨架（四套差异是结构级的，硬参数化反而更难维护），
其余五块皮由 token 派生 —— 保证「换界面不换视觉语言」。

#### 5.7.3 关键词匹配（`ui_presets.match`）

加权打分：前 5 个关键词 12 分、其余 9 分、`fit` 题材 7 分、名称/气质 5 分。

**两个关键设计**：
1. **无命中就返回空**，不硬塞默认值 —— 硬塞会让界面和故事对不上
2. **区分 strong / weak**：只沾到 1 个边缘词时标 `weak`，提醒 agent「气质可能不对，先问用户」

踩过的坑：`江南 雨夜 旧事` 一开始匹配到 `cinema-dark`（因为「雨夜」在它的主关键词里），
而「江南」排在 `ink-classic` 第 11 位、权重低。修法是把**地域/文化词提到主关键词**——
江南比雨夜更能决定风格。

#### 5.7.4 界面 Agent（`ui`，第 5 个 agent）

不挂自动流水线（`PIPELINE_ORDER` 里没有它）：界面是「给一句氛围词马上要看东西」
的交互活，不适合塞进 writer→art→voice→build 那条一次性跑完的线。

5 个工具：

| 工具 | 干什么 |
|---|---|
| `list_ui_presets` | 列四套（id/气质/适合题材/菜单排列） |
| `match_ui_preset` | 关键词打分，返回 score + 命中词 + strength |
| `render_ui` | 用预设渲染一块界面，落 `ui/rendered/<id>-<screen>.html` |
| `tweak_ui_preset` | 只改配色/字号/字距/菜单排列（换预设优先于改配色） |
| `check_ui` | 验收：文件、占位符、菜单项、图片引用、字号、强调色是否真用上 |

skill 4 篇：`SKILL.md`（5 条红线 + 六步作业）+ `01-preset-map`（关键词→预设，含 5 种易误判情况）
+ `02-render`（参数与 5 个真坑）+ `03-gate`（什么算过关、什么必须重做）。

#### 5.7.5 本轮自检抓到的三个真 bug

| bug | 现象 | 修法 |
|---|---|---|
| 弱命中当满分 | 一个「赛博」就给 score 1.0，agent 会误以为完全匹配 | 加 `strength`：命中 ≥2 词或主关键词才 strong |
| `check_ui` 比对错对象 | 微调过强调色后，check_ui 拿**原**预设去比，误报「没套上」 | 改用 `self._ui_preset()`（含 tweaks） |
| 冷色剧场没用强调色 | title 的蓝色全是硬编码 rgba，`$accent` 一次都没用 → 换色无效 | 细线框竖线 + 菜单 hover 改用 `$accent` |

**第三个是 `check_ui` 自己抓出来的** —— 验收关卡真起了作用。

#### 5.7.6 素材从哪来

启动界面的底图和立绘是用**百炼 qwen-image-3.0-pro** 现生的（key 从
`~/.dsh/.credentials.yaml` 的 `DASHSCOPE_API_KEY` 现场读，不进代码、不进日志）：

- `ui/assets/title/bg_port.png` 雨夜港口台阶（1280×720，58 秒）
- `ui/assets/title/bg_city.png` 港口小镇远景 + 灯塔
- `ui/assets/title/char_l.png` / `char_r.png` 白底立绘（1024×1536）

三张图共用一套色系（冷蓝 + 暖黄灯 + 红点缀），所以换哪套预设都不串色。
立绘是**白底 + CSS `mix-blend-mode: multiply`** 压进宣纸，看起来像画在纸上而不是贴上去 ——
这是水墨那套能成立的关键。

---


### 5.1 动手前发现的真相

**之前的 art 不是 agent。** `pipeline.py` 里是硬编码循环：列素材 → 把 writer 写的 `bg_prompt`
原样转发给图像模型 → 查一致性。那 3 个工具从不经过 LLM，**skill 文档写了也没人读**。

立绘提示词是代码拼的（`tools.py` 原第 670 行）：
```python
prompt=f'Galgame 角色立绘，{c["name"]}，半身，正面，干净背景。{notes.get(c["id"], "")}'
```

所以只加 md 文件是白干，必须**文档层 + 架构层一起动**。

### 5.2 文档层：art 从 1 篇 → 7 篇

```
app\skills\art\
├─ SKILL.md           入口：5 条红线、6 步作业顺序、四类资产、工具速查、完成标准
├─ 01-style-bible.md  风格圣经：全片唯一的视觉输入源
├─ 02-background.md   背景：bg_key 归并、地点×时间×天气、无人、复用优先
├─ 03-character.md    立绘：外形锚点逐字复用、2~4 套状态、差分不重画
├─ 04-interface.md    界面：六块皮（title/dialog/choice/save/settings/hud）
├─ 05-typography.md   文字：四级字号、三档字体、可读性底线
└─ 06-gate.md         验收关卡：什么算过关、什么必须重画
```

**加分篇不用改 registry 代码** —— `registry.skill_files()` 会自动抓目录下所有 `*.md`，
`SKILL.md` 排第一，其余按文件名排序。

### 5.3 架构层：4 个新工具

| 工具 | 干什么 |
|---|---|
| `set_style` | 定风格圣经（palette/medium/light/typeface/negative/refs），一部作品只定一次 |
| `draft_prompt` | **核心**：按 target（background/character/ui/typography/fx）拼提示词，自动带画法+光影+色板+负面词 |
| `generate_ui` | 六种界面皮落盘，登记进 `agent_state.ui` |
| `check_style` | 验收关卡：色板≥4色、medium/light 非空、角色 `look` 非空、背景提示词带风格前缀 |

art 工具从 3 → 7，全局 24。

**规范同时落进代码**：`tools.py` 顶部有 `ART_RULES` 常量（每类资产的 prefix / suffix / negative / size）。
没 LLM 时也守同一条线，不会"没模型就随便画"。

### 5.4 契约新增

```python
class Style(Model):
    palette: list[str] = []      # 主 / 辅 / 强调 / 中性
    medium: str = ''             # 画法：赛璐璐 / 厚涂 / 水墨 / 描线
    light: str = ''              # 光影：色温 / 对比 / 主光方向
    typeface: str = ''           # 字体：标题 / 正文 / UI
    negative: list[str] = []     # 全局负面词
    refs: list[str] = []
    version: int = 1

class Character(Model):
    ...
    look: str = ''               # 外形锚点，立绘逐字复用

class Graph(Model):
    ...
    style: Style | None = None   # 全书唯一风格圣经
```

### 5.5 流水线改动

`pipeline.art()` 现在是这样：

```python
await self.ensure_style()                 # 没定就用 DEFAULT_STYLE 中性兜底
pending = await list_pending_assets()
for target, item in items:
    draft = await draft_prompt(target, key, brief=item['prompt'])
    await generate_image(target, key, prompt=draft['prompt'], negative=draft['negative'])
await check_consistency()
await check_style()
```

实测证据：40 节点跑完，**40/40 背景提示词都带风格前缀**：

```
画法赛璐璐，细描线，色块干净，光影中性色调，中对比，柔和顶光，
色调#1f2430 #2b3245 #c4564b #e8e6e3。场景，无人，无文字。…
```

### 5.6 顺手修的 bug

`generate_image` 里 `place = background.get('place') or prompt` ——
prompt 变长后每个节点的 `place` 都不同，**素材库再也判不出"同一个地方"，复用必然失效**。
改成 `or key`（bg_key 兜底）。

---

### 5.8 多 agent 并行第一波（Astra 后端 + Grok 补丁 + 经理接线）

#### 交付了什么

| 角色 | 产物 | 状态 |
|---|---|---|
| **Astra** | `app/api_assets.py`（参考图/语音/素材库 6 个接口）、`app/asset_records.py`（登记表）、`app/assets/cutout.py`（抠图）、`providers/image.py` 支持参考图、`providers/base.py` 修 TTS 时长 | 代码全对，它自己环境跑不了（`_imaging`/`_ssl` DLL 加载失败、`WinError 5`），**验证由经理重跑，全 PASS** |
| **Grok** | `tools/build_unity.py` 重构成 `build_exe()`、`export_unity.py` 加 `export_unity_and_build()`、`export_mock.py` 的 web 播放器改预设标题页 | 真构建成功，日志 `Build Finished, Result: Success.` |
| **经理** | `mock_api.py` 接 Unity 导出线（`Literal` 加 `'unity'` + `UNITY_BUILD_LOCK` 串行 + 真实 exe 路径回报）、`config.py` TTS 默认值改 https、修抠图阈值/形态学、修 `_probe_tts.py` 时长 | 九项回归全过 |

#### 关键验证数据（都是经理亲自跑的）

```
export_game(root, graph, 'unity')  → ok=True  44.7 秒
  exe   D:\UnityProject\Build\奇迹工坊.exe   671,744 字节（启动器本体）
  Build 目录总计 85.1 MB，奇迹工坊_Data 存在
web 导出 4 套预设 → 标记全部一致，play.html 都在
Astra 上传探针 → references / voices / library 三个生命周期全 PASS
抠图 → RGBA alpha (0,255)，char_l/char_r 都能抠
TTS → HTTP 200，2.72 秒真实时长（修之前读成 44739 秒）
```

#### 踩到并修掉的问题

| 问题 | 真因 | 修法 |
|---|---|---|
| TTS 时长 44739 秒 | 流式 WAV 的 `data` chunk 声明 2147483547 字节（占位值） | `audio_seconds()` 按实际字节算，不信头部 |
| 抠图白边 | 阈值 246 太严（生图常带 240 左右的浅色描边），且没做形态学 | 阈值降 236 + 开运算吃 1px + 羽化 0.6px |
| 设置页填不了 TTS | `main.py` 只认 ws/wss，qwen3-tts 走 https | 白名单加 https/http |
| 默认 TTS 一开就不可用 | 默认值是 CosyVoice 的 wss（本机没 websocket 库） | 默认改 `https://dashscope.aliyuncs.com` + `qwen3-tts-flash` |

**抠图仍有一点点极淡的发梢白边** —— 生图自带描边的固有限制，日常背景看不出来，要完美得上专门 matting 模型。

---


**必须带 `PYTHONPATH`**，否则 `tests/*.py` 报 `No module named 'app'`。

```powershell
$env:PYTHONPATH = 'D:\软件\GalgameStudio'
$py = 'D:\软件\GalgameStudio\app\.venv\Scripts\python.exe'

# 1. agent 配置自检（skill 篇数 / 工具白名单 / description）
& $py -m app.agents.registry
#    期望：✓ 4 个 agent，24 个工具，skill 齐全
#         art skill=7 篇 tools=7

# 2. skill 拼装 + 文档工具名零缺失
& $py tools\_check_art_skill.py
#    期望：结果: PASS（7 篇全进 system prompt，未定义工具 []）

# 3. 工具行为 7 条判据
& $py tools\_test_art_style.py
#    期望：结果: PASS

# 4. 流水线真路径（40 节点）
& $py tools\_test_agent_real_path.py 40
#    期望：全 ✓，节点 40，47 次工具调用

# 5. 产物里背景有没有风格前缀
& $py tools\_verify_art_product.py
#    期望：背景提示词带风格前缀: 40 / 40，结果: PASS

# 6. 契约
& $py tests\check_contract.py
#    期望：全部 PASS，1098 组合

# 7. 主流程
& $py tests\test_m1.py
#    期望：Ran 6 tests OK

# 8. 界面预设自检（字段/配色/关键词/文件名与 id 一致）
& $py -c "from app.ui_presets import check, all_presets; ps=all_presets(); print(len(ps),'套', check() or 'PASS')"
#    期望：4 套 PASS

# 9. 界面 Agent 端到端（27 条判据）
& $py tools\_test_ui_agent.py
#    期望：结果: PASS  (27/27)
```

九项全过才算没破。**任何一项 FAIL 先修，别往下做。**

---

## 7. 坑（踩过的，别重踩）

### 环境

| 坑 | 正确做法 |
|---|---|
| 后端默认把凭据写 `%LOCALAPPDATA%\GalgameStudio`，那路径**能建目录不能写文件**（WinError 5） | **必须**设 `GAL_STATE_DIR=D:\软件\GalgameStudio\.state`，否则 `PUT /api/providers` 直接 500 |
| 装 `uvicorn[standard]` 会让 pip 依赖解析卡死（实测烧 500+ 秒 CPU） | 只装纯 `uvicorn` |
| 前端 `file://` 打开时 `Origin` 是 `null` | 后端必须开 CORS `allow_origins=["*"]` |
| `app/config.py` 在 import 那一刻就把 ROOT 定死 | **先**设环境变量再 import app |
| `tests/*.py` 从 `tests\` 跑报 `No module named 'app'` | 先 `$env:PYTHONPATH='D:\软件\GalgameStudio'` |
| PowerShell 长内联脚本带中文会崩 AMSI（`AccessViolationException ... AmsiScanBuffer`） | 写成 `.py` 文件用 python 跑，别在命令行里堆中文 |

### 无头 Edge 截图（做界面验收必踩）

| 坑 | 正确做法 |
|---|---|
| **用 `subprocess` 从 python 起 Edge 必卡死**（`capture_output=True` 管道满 / profile 单例锁，实测两次都超时 90~120 秒） | **一律用 pwsh 直接起 Edge**（1 秒出图）；python 只负责生成 HTML |
| 沙箱受限时报 `mojo ... platform_channel.cc:187 Check failed: 拒绝访问`（0x5） | Chromium 多进程 IPC 被拦，需要 danger-full-access；`--single-process` 也绕不过 |
| `$ErrorActionPreference='Stop'` 会让整个循环中断 | Edge 往 stderr 打一堆无关警告（QQBrowser 路径、crashpad），**别设 Stop**，末尾用 `2>$null` 或 `\| Out-Null` |
| 复用的 `--user-data-dir` 会让新实例把请求转给老实例、自己不退出 | 每次用**新 profile 目录**，跑完删 |

### 前端

| 坑 | 正确做法 |
|---|---|
| `ui/vendor/litegraph.js` 是第三方库 | **不许改**，0.7.18 |
| 样式要求"和之前一模一样，只抄节点连接逻辑" | 别顺手改视觉 |

### 契约

| 坑 | 正确做法 |
|---|---|
| `Model` 是 `extra='forbid'` | 加字段必须给默认值，否则老数据全报错 |
| `ending` 节点不能有 `choices` | 有 `model_serializer` 自动剔除 `null` |
| `edges[].from` 是 Python 关键字 | 用 `from_` + `alias='from'` |

### 执行环境（DSH 会话内特有，产品本身没这问题）

| 坑 | 正确做法 |
|---|---|
| **`.venv\Scripts\python.exe` 写 `D:\UnityProject`、`D:\AI工作站` 报 `PermissionError 13`**，但 pwsh 和系统 python 都能写 | 这是 DSH 会话给的解释器沙箱范围（只放行 workspace `D:\软件`）。**产品运行时（用户双击 launcher）不受影响** —— 端到端验 Unity 导出请用**系统 python**：`C:\Users\q3153\AppData\Local\Programs\Python\Python311\python.exe` |
| 外部 agent 报 `_imaging` / `_ssl` DLL 加载失败 | 它们自己执行环境的毛病，同一份代码在 `.venv` 里 `import PIL` / `import ssl` 正常。**别据此改代码**，先在本机复跑 |
| `python -c "..."` 内联脚本在 PowerShell 里被吞引号 | 一律写成 `.py` 文件再跑（这条反复踩） |

### Unity

| 坑 | 正确做法 |
|---|---|
| 全屏纯洋红 `#FF00FF` | 两个原因：① `EmptyScene` 没相机，要加 `cullingMask=0` 的清屏相机；② UGUI shader 被剔除，要用 `Resources` 放 keeper 材质 |
| 40px 黑边 | `defaultIsNativeResolution = true` + `Screen.SetResolution`，别设 `defaultScreenHeight` |
| 按钮全无反应 | Canvas 缺 `GraphicRaycaster` + 场景缺 `EventSystem` |
| `Builder.cs` 放 `Assets/` 根会编进 Runtime 程序集报 CS0234 | 必须放 `Assets/Editor/` |
| 把 shader 加进 `GraphicsSettings.m_AlwaysIncludedShaders` 会 `m_LockCount == 0` 构建失败 | **别用那个列表**，用 Resources 材质 |
| `Screen.fullScreen = true` 会覆盖 `FullScreenWindow` 变成独占全屏 | 删掉这行 |
| 截图全是桌面壁纸（同一 sha256） | 150% DPI 下必须 `SetProcessDpiAwarenessContext(-4)`，且 D3D 窗口用 `PrintWindow` 拿不到 |
| `Start-Process` 起 Electron / 游戏会立刻退出 | 用 `explorer.exe <路径>` |
| 截屏时屏保抢前台 | `AttachThreadInput` + `SetForegroundWindow`，并用 `GetForegroundWindow()` 断言 |

---

## 8. 下一步优先级

1. **前端（Kimi 还没交）**：参考图上传区、语音标注、素材库面板、导出页加 Unity 按钮
   —— 后端接口已就绪，只剩 UI
2. **表情 / 动作差分**：`Character` 现在只有 `look` + 一张 `ref_image`，
   要加 `poses` 数组（**改 `contract.py` 必须给默认值**，extra='forbid'）
3. **Unity 播放器接素材库**，现在还是占位色块
4. 参考图 >10 MB 时压缩后再送给百炼（契约允许 20 MB，模型上限 10 MB）
5. `stats.cost` 真算钱
6. 清理 `projects\` 下约 60 个测试项目 + 一堆 `tmp*` / `pip-*` 临时目录

> 已完成：界面四套预设（§5.7）、参考图/语音上传接口、抠图、参考图生图、
> Unity 一键出 exe、TTS 时长修复（§5.8）。

---

## 9. 一句话备忘

**这套东西的核心不是"能生成图"，是"所有图都听同一份风格圣经"。**
任何一个新资产类型（对话框、图标、特效、字体样本），
正确做法都是：**先加 `ART_RULES` 规则 → 加工具 → 写 skill 分篇 → 跑验收**，
而不是直接往代码里硬编提示词。
