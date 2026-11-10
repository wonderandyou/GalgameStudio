# Kimi · 前端任务书

## 你是谁

你是这个项目的**前端负责人**。项目是一个「全自动 galgame 制作器」：
用户输入一句需求 → 多个 Agent 生成剧情/立绘/背景/语音 → 用户在网页界面里编辑节点图 → 导出成可玩的游戏。

你是四个执行者之一，只负责 `ui\`。**后端由 Astra 负责，你们照同一份接口契约写。**

---

## 一、硬性前提（先读这段，不然白干）

1. **工作区**：`D:\软件\GalgameStudio`
2. **你只能改 `ui\` 下任何文件**。`app\` / `tools\` / `projects\` 一律不碰。
3. **前端是纯 HTML/CSS/JS，没有构建**。别引入 npm、别加打包、别用需要编译的语法
   （现有代码是 ES5/ES2017 混写的普通 `<script>`，能跑就行）。
4. **先读这三份再动手**：
   - `docs\协作\00-团队规则.md`（文件边界 + 禁令）
   - `docs\协作\10-接口契约.md`（**你要对接的接口全在这里**）
   - `ui\api.js`（现有接口层，新接口按它的风格加）
5. **不许 `git` 操作**、不许 `pip install`、不许删 `projects\`。

---

## 二、前端现状（别重造已有的东西）

| 文件 | 大小 | 作用 |
|---|---|---|
| `ui\index.html` | 35 KB | 三段视图：项目列表 / 编辑器 / 导出 |
| `ui\api.js` | 17 KB | 接口层，`DEFAULT_BASE = 'http://127.0.0.1:8790'`（第 11 行） |
| `ui\app.js` | 152 KB | 主逻辑（节点图、SSE 进度、自动保存） |
| `ui\app.css` | 46 KB | 样式 |
| `ui\graph-editor.js` | 26 KB | LiteGraph 封装 |
| `ui\vendor\litegraph.js` | — | **第三方库，绝对不许改** |

**已经能用的**：新建项目、输入需求、生成、SSE 实时进度、自动保存（`setSaveBadge`）、
读素材（`/api/assets/{pid}/{path}`）、导出 renpy / web。

**怎么跑起来看效果**：

```powershell
# 1) 起后端（后端没起来页面会显示「接口未连接」）
powershell -ExecutionPolicy Bypass -File D:\软件\GalgameStudio\scripts\run-backend.ps1

# 2) 直接打开前端（file:// 也能跑，后端已开 CORS）
start "" "D:\软件\GalgameStudio\ui\index.html"
```

---

## 三、你要做的四件事

### 任务 1：参考图上传区（最高优先）

位置：编辑器视图的侧边栏，或新建一个「素材」页签 —— **你判断放哪最顺手**。

要能上传**两类**参考图：
- **角色立绘参考**（用户自己的画，Agent 要照着画角色）
- **场景风格参考**（Agent 要照着这个画风做背景）

要求：
- 拖拽 + 点选两种方式都能传
- 传完立刻显示缩略图、名字、大小，能删
- 两类分开显示，不能混在一起
- 上传中要有进度/等待态，失败要能看到原因（契约里的 `error.code` / `error.message`）
- 接口：`POST/GET/DELETE /api/projects/{pid}/references`（见契约 §1）

**这一条是整个产品的根**：没有参考图，「仿参考图画风」这条需求完全无从谈起。

### 任务 2：语音素材上传 + 标注角色

用户会**自己上传语音素材**（比如自己录的、或别处找的），然后把某段语音标注给某个角色。

要求：
- 上传音频（wav/mp3/ogg），显示时长（后端会返回真实 `seconds`）
- 上传后能选「这段属于哪个角色」——角色列表从 `graph.characters` 取
- 能改标注、能删
- 接口：`POST/GET/DELETE /api/projects/{pid}/voices`（见契约 §2）

### 任务 3：素材库面板

一个能看到项目里**所有已生成/已上传素材**的面板：

- 四类分组：背景（bg）/ 立绘（char）/ 参考图（ref）/ 语音（voice）
- 每类显示缩略图或音频条，点开能放大/试听
- 素材路径拼 `/api/assets/{pid}/{asset}` 就能读
- 接口：`GET /api/projects/{pid}/library`（见契约 §3）

**注意**：现在前端**没有**任何素材库 UI，这是从零加。

### 任务 4：导出页加 Unity

现有导出页（`ui\index.html` 第 457 行起的「视图 3」）只有三个按钮：
`导出 Ren'Py` / `导出网页版` / `导出录像`。

要加：**导出 Unity（.exe）**

- 提交 `POST /api/projects/{pid}/export`，`format: "unity"`
- 这个会**很慢**（Unity 构建 3~10 分钟），所以必须有：
  - 明确的「正在构建，别关窗口」提示
  - 进度/等待态（可以复用 SSE 的 job 事件，也可以只做轮询）
  - 失败时把后端 `error.message` 原样显示出来
- 后端构建逻辑由 **Grok** 实现，你只管前端

---

## 四、验收（必须自己截图，不许只说"改好了"）

```powershell
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$ui = 'D:\软件\GalgameStudio\ui'
$out = 'D:\软件\GalgameStudio\docs\screens\kimi'
New-Item -ItemType Directory -Force -Path $out | Out-Null

# 起后端
powershell -ExecutionPolicy Bypass -File D:\软件\GalgameStudio\scripts\run-backend.ps1

# 截图（注意：Edge 必须用 pwsh 直接起，用 python subprocess 会卡死——踩过）
& $edge --headless=new --disable-gpu --no-first-run --hide-scrollbars `
  --force-device-scale-factor=1.5 --virtual-time-budget=4000 --window-size=1600,900 `
  --user-data-dir="$out\_prof" --screenshot="$out\素材页.png" `
  "file:///D:/软件/GalgameStudio/ui/index.html"
```

**要交的截图**：参考图上传区（空 / 有图）、语音标注、素材库面板、导出页带 Unity 按钮。

**自检清单**（做不到就不算完）：
- [ ] 页面不白屏、控制台无红色报错
- [ ] 上传能真传成功（后端 `projects\<pid>\assets\ref\` 下出现文件）
- [ ] 删掉后列表立刻更新
- [ ] 上传失败时用户能看到**具体**原因，不是「操作失败」
- [ ] 截图存到 `docs\screens\kimi\`

---

## 五、和后端的配合

- **后端可能还没做完**（Astra 在并行做）。这时候你按契约**照写**，
  用假数据先把界面做出来，别停下来等。
- 如果发现契约和实际不一致 → **不要自己改形状**，写进 `docs\handoff\kimi.md`
  的「发现但没改的」一栏，经理统一协调。
- 联调时后端没起来，页面要显示「接口未连接」而不是报一堆错 —— 现有代码已有这个处理。

---

## 六、交活

最后写 `docs\handoff\kimi.md`（格式见 `docs\协作\90-交接格式.md`）。

**没写 handoff 等于没做完。**
