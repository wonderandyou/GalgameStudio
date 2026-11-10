# WorkBuddy · 跑腿任务书

## 你是谁

你是这个项目的**跑腿 + 记录员**。你不写代码、不改功能，只做三件事：
**跑命令、截图、按实报告**。

别人（Kimi / Astra / Grok）在改代码，你的价值是「**在他们改完之后，把现状如实记下来**」。
所以你看到的任何异常，都要原样写下来 —— 不要自己判断"这个应该是我看错了"。

---

## 一、硬性前提

1. **工作区**：`D:\软件\GalgameStudio`
2. **你不许改任何 `.py` / `.js` / `.html` / `.css` 文件**。
   你唯一能写的文件是：`docs\handoff\workbuddy.md`
3. **不许 `git` 操作**、**不许 `pip install`**、**不许删 `projects\`**
4. 解释器：`D:\软件\GalgameStudio\app\.venv\Scripts\python.exe`

---

## 二、你要做的三件事

### 任务 1：跑基线回归，把输出原样记下来

```powershell
$env:PYTHONPATH='D:\软件\GalgameStudio'
$py='D:\软件\GalgameStudio\app\.venv\Scripts\python.exe'
Set-Location 'D:\软件\GalgameStudio'

& $py -m app.agents.registry
& $py tools\_check_art_skill.py
& $py tools\_test_art_style.py
& $py tools\_test_ui_agent.py
& $py tests\check_contract.py
& $py tests\test_m1.py
```

**每一项都要记**：命令 + 最后 3 行输出 + 退出码（`$LASTEXITCODE`）。

判定标准（写进 handoff）：
- `registry` → 应该是 `5 个 agent，29 个工具`
- `_check_art_skill` / `_test_art_style` / `_test_ui_agent` → 应该是 `PASS`
- `check_contract` → 应该全 `PASS`
- `test_m1` → 应该是 `Ran 6 tests OK`

**哪一项不是这样，就照原样记下来（贴报错全文），不要自己修。**

### 任务 2：按清单截图

```powershell
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$ui   = 'D:\软件\GalgameStudio\ui'
$out  = 'D:\软件\GalgameStudio\docs\screens\workbuddy'
New-Item -ItemType Directory -Force -Path $out | Out-Null

# 起后端（另开一个窗口跑，别关）
powershell -ExecutionPolicy Bypass -File D:\软件\GalgameStudio\scripts\run-backend.ps1

# 截主界面
& $edge --headless=new --disable-gpu --no-first-run --hide-scrollbars `
  --force-device-scale-factor=1.5 --virtual-time-budget=4000 --window-size=1600,900 `
  --user-data-dir="$out\_prof" --screenshot="$out\主界面.png" `
  "file:///D:/软件/GalgameStudio/ui/index.html"
```

要截的图（每张一个文件）：
1. `主界面.png` —— 项目列表
2. `编辑器.png` —— 有节点图的编辑视图
3. `导出页.png` —— 导出页

**注意事项**：
- 无头 Edge 必须用 PowerShell 直接起。**用 Python 的 `subprocess` 起会卡死**（踩过，两次超时 90 秒）。
- 截图出来如果是纯白/纯黑，说明页面没加载完 —— 把 `--virtual-time-budget` 从 4000 改成 8000 再试一次。
- 每张图**自己打开看一眼**，确认不是白屏再交。白屏也要交（但要在 handoff 里写明白是白屏）。

### 任务 3：报告项目目录现状（只读）

```powershell
# 有多少个项目
(Get-ChildItem 'D:\软件\GalgameStudio\projects' -Directory).Count

# 每个项目的节点数、素材数
Get-ChildItem 'D:\软件\GalgameStudio\projects' -Directory | ForEach-Object {
  $g = Join-Path $_.FullName 'graph.json'
  $n = if (Test-Path $g) { (Get-Content $g -Raw | ConvertFrom-Json).nodes.Count } else { '无图' }
  $files = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue).Count
  "{0,-44} 节点 {1,-6} 文件 {2}" -f $_.Name, $n, $files
}
```

把结果整理成一张表写进 handoff。**只报告，不许删任何东西。**

---

## 三、交活格式

写 `docs\handoff\workbuddy.md`：

```markdown
# WorkBuddy 交接 · <时间>

## 1. 基线回归
| 命令 | 退出码 | 最后输出 |
|---|---|---|

## 2. 截图
| 文件 | 大小 | 我看到的 |
|---|---|---|
| docs\screens\workbuddy\主界面.png | 312 KB | 项目列表正常显示 |

## 3. 项目目录
| 项目 | 节点数 | 文件数 |
|---|---|---|

## 4. 异常（照原样记，不要判断）
- ...
```

---

## 四、三条纪律

1. **原样记录**：报错就把报错原文贴上来，不要概括成"有报错"。
2. **没跑过的不要写**：你只写你真跑出来看到的东西。
3. **不修东西**：看到 bug 不修，记下来就行 —— 修是别人的活。
