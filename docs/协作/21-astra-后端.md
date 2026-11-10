# Astra · 后端任务书

## 你是谁

你是这个项目的**后端负责人**。项目是「全自动 galgame 制作器」：
用户输入一句需求 → Agent 生成剧情/立绘/背景/语音 → 网页界面编辑节点图 → 导出可玩游戏。

你是四个执行者之一，只负责 `app\`。**前端由 Kimi 负责，你们照同一份接口契约写。**

---

## 一、硬性前提

1. **工作区**：`D:\软件\GalgameStudio`
2. **你只能改 `app\` 下的文件**，除了这三个例外不许碰：
   - `app\contract.py` ← **契约，只有经理能改**（`extra='forbid'`，加字段必须给默认值，改错全盘崩）
   - `app\engine\export_*.py` ← Grok 负责
   - `ui\` / `tools\` / `projects\` ← 一律不碰
3. **解释器只有一个**：`D:\软件\GalgameStudio\app\.venv\Scripts\python.exe`（3.11.9）
4. **Pillow 13.0.0 已装好**，抠图直接 `from PIL import Image`。
   **不许再 pip install 任何东西** —— 确需新包写进 handoff，经理来装。
5. **不许 `git` 操作**、不许删 `projects\`。
6. **先读这三份**：
   - `docs\协作\00-团队规则.md`
   - `docs\协作\10-接口契约.md`（**新接口的形状全在这里，照它写**）
   - `HANDOFF.md` §0（产品目标流程，判断"打通没打通"的标准）

---

## 二、架构现状（别重造）

```
app\
├─ main.py              FastAPI 入口；只有 4 个路由（health/providers×3）
├─ mock_api.py          ★ 11 个业务接口都在这里（APIRouter prefix='/api'）
├─ contract.py          ★ 数据契约，pydantic Model，extra='forbid'
├─ config.py            ROOT 在 import 那一刻定死，必须先设环境变量
├─ store.py             项目读写、atomic_json / atomic_bytes
├─ agents\              registry（5 个 agent）/ tools.py（工具实现）/ loop.py（LLM 循环）
├─ engine\
│  ├─ pipeline.py       流水线 writer → art → voice → build
│  ├─ graph.py          validate_graph
│  ├─ export_mock.py    导出 renpy / web
│  └─ export_unity.py   导出 Unity 用的 StreamingAssets（独立脚本，没接 API）
├─ providers\
│  ├─ base.py           BaseProvider / ProviderError / audio_seconds / redact
│  ├─ image.py          ★ DashScopeImageProvider（现在**纯文生图**）
│  └─ tts.py            BailianTTSProvider（qwen3-tts-flash，实测可用）
└─ ui_presets\          界面风格预设（4 套）+ 渲染器
```

**接口风格**（必须照抄）：
- 成功返回 `dict`（`{"ok": True, ...}`）
- 失败抛 `ProviderError(code, message, where, status)`，全局处理器转成 `{"error": {...}}`
- 文件落盘：`atomic_json` / `atomic_bytes`
- 大文件读写真要用 `await asyncio.to_thread(...)`，别阻塞事件循环

---

## 三、你要做的六件事（按优先级，前面的别跳过）

### 任务 1：TTS 时长读错（最急，一个函数）

**现象**（已验证）：`qwen3-tts-flash` 返回的 WAV 是**流式写出来的**，`data` chunk 的大小是占位值：

```
data chunk 声明大小 = 2147483547   ← 约 2 GB，假的
实际剩余字节        = 126720
真实时长            = 126720 / (24000 × 1 × 2) = 2.64 秒
而 audio_seconds() 读出来 = 44739 秒（12.4 小时）
```

**后果**：`graph.dur` 会被写进几万秒，播放节奏彻底崩。TTS 现在**能出声但不能用**。

**要修**：`app\providers\base.py` 的 `audio_seconds(wav_bytes)`。
不许信任头部声明的块大小，按「实际字节数 − 头部开销」算；或读到 `data` 块后
用 `len(data) - (chunk起点+8)` 当真实数据长度。

**验收**：`tools\_probe_tts.py` 能跑，且它打印的时长在 1~10 秒之间（不是几万秒）。

### 任务 2：参考图上传接口

用户上传**自己的**角色立绘参考图、场景风格参考图。这是整个产品的根 ——
没有它，「仿参考图画风」这条需求完全无从谈起。

按 `docs\协作\10-接口契约.md` §1 实现三个接口：

```
POST   /api/projects/{pid}/references          上传（multipart）
GET    /api/projects/{pid}/references          列表
DELETE /api/projects/{pid}/references/{id}     删除登记
```

要点：
- 落盘 `projects/{pid}/assets/ref/{kind}-{sha16}.png`（内容寻址，重复上传不重复写）
- 登记 `projects/{pid}/refs.json`
- **参考图不进 `assets/bg` / `assets/char`** —— 那两个目录是生成产物，
  混进去会让「按 bg_key 复用背景」的逻辑错乱
- 校验魔数（PNG `\x89PNG` / JPEG `\xff\xd8`）、大小 ≤ 20 MB
- 新接口建议放**新文件** `app\api_assets.py`（`APIRouter(prefix='/api')`），
  在 `main.py` 里 `include_router` —— 别往 586 行的 `mock_api.py` 里继续堆

### 任务 3：生图支持参考图（这一步决定画风能不能仿）

**现状**：`app\providers\image.py` 走的是
`{base}/compatible-mode/v1/images/generations`（OpenAI 兼容），**只能文生图**，
`call(prompt, width, height)` 里没有任何参考图参数。

**要做的**：让 `DashScopeImageProvider` 能带参考图生成。

已知事实：
- 同一把 key、同一个域名，还有一条 `POST /api/v1/services/aigc/multimodal-generation/generation`
  路径，它的 `input.messages[0].content` 是**多模态数组**，可以同时放 `image` 和 `text`
- 该路径本机实测可用（生成过图），且 `image` 支持 data URI（`data:image/png;base64,...`）
- 具体字段形状**你要自己实测确认**，别照抄我的猜测

要求：
- `call()` 增加可选参数（如 `reference_images: list[bytes]`、`style_hint: str`）
- 不带参考图时行为**和现在完全一致**（不能把已经能用的路径改坏）
- 带参考图时，产物要能看出**画风跟着参考图走**
- 失败要明确：`REFERENCE_NOT_SUPPORTED` / `REFERENCE_TOO_LARGE` 之类，别静默降级成纯文生图

**验收**（关键，必须做）：
拿一张参考图（`ui\assets\title\char_l.png` 就可以）+ 一句 prompt 真调一次，
把结果存到 `docs\screens\astra\`，**用眼睛对比**：风格是不是跟参考图接近了。
只跑通不算，要看出来。

### 任务 4：抠图（立绘去背景 → 透明 PNG）

**现状**：完全没有。生成的立绘是白底方图，直接贴进节点会出现白块。

**要做**：一个可复用的抠图函数 + 接进生成流程。

技术方向（自己选，能用就行）：
- Pillow 已装。推荐：**从四边洪泛填充**找背景色 → 按颜色距离做阈值 → 保留主体 → 边缘羽化 1~2 px
- 备选：生成时让模型出**纯色背景**（如纯绿 `#00FF00`），再做 chroma key
- 目标：`RGBA`，人物边缘**不能有白边**（这是最常见的翻车点）

**验收**：
拿 `ui\assets\title\char_l.png` 和 `char_r.png` 各跑一遍，
输出放到 `docs\screens\astra\抠图-前.png` / `抠图-后.png`（后者叠在深色背景上才能看出白边），
**自己看图检查有没有白边、有没有把人物白色部分抠掉**。

### 任务 5：语音素材上传接口

按契约 §2 实现：

```
POST   /api/projects/{pid}/voices    上传（multipart: file, character_id, label）
GET    /api/projects/{pid}/voices    列表
DELETE /api/projects/{pid}/voices/{id}
```

要点：
- 落盘 `assets/voice/{sha16}.wav`
- 登记 `projects/{pid}/voices.json`
- **`seconds` 必须按任务 1 修好的算法算**，不许再信头部
- 非 wav 要么转码（本机有 ffmpeg 就用），要么明确返回 `NEED_WAV 415`，不许静默改名

### 任务 6：素材库列表接口

```
GET /api/projects/{pid}/library
```

按契约 §3。**扫磁盘真实文件**（`assets/{bg,char,ref,voice}/`），不要只读 json 登记表
—— 磁盘才是真相。

---

## 三·五、`export` 的 unity 分支不要你接

`app\mock_api.py` 第 61 行 `class Export(BaseModel)` 的
`Literal['renpy', 'web', 'video']` 里要加 `'unity'`，以及 export 函数里那个分派
—— **这两处由经理在融合时改**，你**不要碰**。

原因：同一个文件里 Grok 也要动，两人同时改必然冲突。
你只要保证「别把它改坏」就行。

如果 Astra 你需要在 `main.py` 里 `include_router(api_assets.router)`，
那是 `app\` 范围内的事，可以改 `main.py`。

---

## 四、验收（做不到就不算完）

```powershell
$env:PYTHONPATH='D:\软件\GalgameStudio'
$py='D:\软件\GalgameStudio\app\.venv\Scripts\python.exe'
Set-Location 'D:\软件\GalgameStudio'

# 1) 基线回归：改了东西不能把旧的弄坏
& $py -m app.agents.registry        # 期望 5 个 agent / 29 工具
& $py tools\_test_art_style.py      # 期望 PASS
& $py tools\_test_ui_agent.py       # 期望 PASS (27/27)
& $py tests\check_contract.py       # 期望全 PASS
& $py tests\test_m1.py              # 期望 Ran 6 tests OK

# 2) TTS 时长
& $py tools\_probe_tts.py           # 期望时长在 1~10 秒
```

**还要自己写一个探针** `tools\_test_upload_astr.py`（或任何你喜欢的名字），
把「上传参考图 → 列表 → 删除」「上传语音 → 列表」真跑一遍，
把输出贴进 handoff。**不许只测代码路径、不测真实请求。**

---

## 五、边界提醒

- 前端可能正在改 `ui\`，**你一个字都不要碰**
- 接口形状以契约为准；你要改形状 → 写进 handoff，别自己改
- 需要动 `contract.py`（比如给角色加 `poses` 差分字段）→ 写进 handoff，
  **经理来改**，因为它 `extra='forbid'`，改错会让所有老项目读不出来
- Unity 导出 / 构建 → 是 Grok 的活，别抢

---

## 六、交活

写 `docs\handoff\astra.md`（格式见 `docs\协作\90-交接格式.md`），必须包含：
每项任务的**真实命令 + 关键输出**。写了「应该没问题」的按没验证处理。

**没写 handoff 等于没做完。**
