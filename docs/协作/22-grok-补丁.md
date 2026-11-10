# Grok · 补丁 / 缝任务书

## 你是谁

你是这个项目的**补丁工程师**，专做「跨层的缝」—— 两个模块各自都对，
但中间没接上的地方。你不做从零的功能，你负责让已有的东西串起来。

项目是「全自动 galgame 制作器」：用户输入需求 → Agent 生成剧情/立绘/背景/语音
→ 网页界面编辑节点图 → **导出成可玩的 .exe**。

最后这一步现在断了，断开的地方就是你的活。

---

## 一、硬性前提

1. **工作区**：`D:\软件\GalgameStudio`
2. **你只能改这三处**：
   - `app\engine\export_unity.py`（Unity 导出）
   - `app\engine\export_mock.py`（renpy / web 导出）
   - `tools\`（构建脚本）
   - `D:\UnityProject\`（Unity 工程）
3. **绝对不许碰**：
   - `ui\`（Kimi 的地盘）
   - `app\mock_api.py`（Astra 的地盘）—— **导出路由的接线由经理做，你别动**
   - `app\contract.py`、`app\providers\*`（Astra / 经理的地盘）
   - `projects\` 不许删
4. **不许 `git` 操作**、**不许 `pip install`**。
5. **先读**：`docs\协作\00-团队规则.md` + `HANDOFF.md` §0 和 §7（Unity 那一栏的坑）。
6. **磁盘只剩 25 GB** —— 构建产物、临时工程别堆太多份，用完清理。

---

## 二、现状：出 .exe 这条路现在是断的

```
图（graph.json）
   ↓  export_game(root, graph, format)      ← 只分派 renpy / web，没有 unity
   ↓  export_unity(root, graph)             ← 函数存在，但没人调它（独立脚本）
   ↓  产出 export/unity/{StreamingAssets, Scripts}
   ↓  tools\_build_unity.py                 ← 手动跑：建工程 → 复制 → Unity 构建
   ↓  <工程>\Build\奇迹工坊.exe
```

**问题**：三处都要人手跑，而且 `_build_unity.py` 是纯命令行脚本，
没法被 API 调用。用户点「导出 Unity」时后端无路可走。

**目标**：把这条链变成一个**能被 import 调用的函数**，一次调用从 graph 走到 exe。

---

## 三、你要做的事

### 任务 1：把构建提成函数（核心）

在 `app\engine\export_unity.py` 里提供：

```python
def export_unity_and_build(root, graph, *, job_dir=None, timeout=1800) -> dict:
    """
    一步到位：graph → StreamingAssets + Scripts → Unity 命令行构建 → .exe

    返回：
      {"ok": True,
       "out": "D:\\UnityProject\\Build\\奇迹工坊.exe",
       "unity_project": "D:\\UnityProject",
       "build_log": "…最后 40 行…",
       "seconds": 512.3}
    失败抛 ValueError / RuntimeError，消息里要带 Unity 日志的尾部片段
    """
```

要求：
- 内部复用 `tools\_build_unity.py` 的逻辑 —— 但**把它重构成函数**，
  别复制粘贴第二份（两份迟早不同步）
- 路径全部可配置：Unity 编辑器 `D:\Unity\6000.0.85f2\Editor\Unity.exe`、
  Unity 工程 `D:\UnityProject`、产物目录
- Unity 命令用无头模式（参考现有脚本，`-batchmode -quit -executeMethod Builder.BuildWindows`）
- **必须有超时和日志捕获**：构建可能 3~10 分钟，卡住要能报出哪一步
- 构建失败时**把 Unity 日志尾部返回**，别只抛 "构建失败"

### 任务 2：`_build_unity.py` 改成可复用

现在它是纯脚本。改成：既能 `python tools\_build_unity.py [项目id]` 跑，
也能 `from tools._build_unity import build_exe` 被 import。
核心逻辑放在 `build_exe(project_root, unity_project, unity_exe, timeout)` 里。

### 任务 3：web 导出的标题页用上界面预设

`app\engine\export_mock.py` 的 `export_web()` 会生成一个网页播放器。
现在它的标题页是写死的。要改成：用 `app\ui_presets` 里那四套预设渲染，
选哪套由项目数据决定（`graph.style` 或导参），挑不出来就用默认那套。

```python
from app.ui_presets import all_presets, render   # 或相对导入
```

验收：导出一个 web 包，打开 `export/web/index.html`，标题页明显是四套之一
（有竖排/横排/斜切/居中的结构差别），不是原来那个写死的。

---

## 四、验收（必须真跑，这个很慢，留足时间）

```powershell
$env:PYTHONPATH='D:\软件\GalgameStudio'
$py='D:\软件\GalgameStudio\app\.venv\Scripts\python.exe'
Set-Location 'D:\软件\GalgameStudio'
```

### 4.1 真构建一次（不能只看代码）

```powershell
# 用一个已有项目当样本
& $py -c "import sys; sys.path.insert(0,r'D:\软件\GalgameStudio'); from app.engine.export_unity import export_unity_and_build; import json,pathlib; root=pathlib.Path(r'D:\软件\GalgameStudio\projects\p_63b2b758e0ab46d1bf4201629468eb36'); g=json.loads((root/'graph.json').read_text(encoding='utf-8')); print(json.dumps(export_unity_and_build(root,g), ensure_ascii=False)[:800])"
```

**产物必须真的存在**：`D:\UnityProject\Build\奇迹工坊.exe`（约 89 MB）。
拿 `Test-Path` 和文件大小证明，不要只说"构建成功"。

启动一下 exe，确认能开出窗口（截图存 `docs\screens\grok\`）。

### 4.2 基线回归

```powershell
& $py tests\check_contract.py     # 全 PASS
& $py tests\test_m1.py            # Ran 6 tests OK
```

---

## 五、踩过的坑（别重踩）

| 坑 | 正确做法 |
|---|---|
| `Screen.fullScreen = true` 覆盖 `FullScreenWindow` 变成独占全屏 | 删掉那行 |
| 40px 黑边 | `defaultIsNativeResolution = true` + `Screen.SetResolution` |
| 全屏纯洋红 | `EmptyScene` 没相机 → 加 `cullingMask=0` 的清屏相机；UGUI shader 被剔除 → Resources 里放 keeper 材质 |
| `Builder.cs` 放 `Assets\` 根会报 CS0234 | 必须放 `Assets\Editor\` |
| 把 shader 加进 `GraphicsSettings.m_AlwaysIncludedShaders` → `m_LockCount == 0` 构建失败 | 别用那个列表，用 Resources 材质 |
| `Start-Process` 起 Unity 会立刻退出 | Unity 构建用 `subprocess.run` 等它；游戏本体用 `explorer.exe <路径>` |
| Unity 首次创建工程要导入内置包，3~10 分钟 | 别设太短超时，报进度 |
| 用 `capture_output=True` 调长输出程序会死锁 | 写日志**到文件**再读，或输出重定向到文件 |

---

## 六、交活

写 `docs\handoff\grok.md`（格式见 `docs\协作\90-交接格式.md`），必须有：

- `export_unity_and_build` 的**函数签名**
- 真构建一次的**耗时和 exe 大小**
- Unity 日志里**成功的那几行**（证明是真空跑出来的）
- 给经理的**接线说明**：`mock_api.py` 里那句 dispatch 该怎么写（你自己别改）

**没写 handoff 等于没做完。**
