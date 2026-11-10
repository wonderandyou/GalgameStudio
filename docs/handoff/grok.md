# Grok（补丁）交接 · 2026-11-07 21:05（UTC+8）

## 我做了什么
- `tools\_build_unity.py`：重构成可复用函数 `build_exe(project_root, unity_project=PROJECT, unity_exe=None, timeout=1800, *, log_dir=None, echo=print) -> dict`。命令行 `python tools\_build_unity.py [项目id]` 仍可用（`main()` 只是调 `build_exe`），也能 `from tools._build_unity import build_exe`。
  - Unity 自己写 `-logFile`，stdout/stderr 重定向到另一个 `*.stdout.log`（原脚本两者写同一个文件会互相覆盖），全程不用 `capture_output`。
  - `timeout` 是“建工程 + 构建”的总预算；超时会杀掉 Unity 并报出是哪一步。
  - 构建前先删旧 exe，防止“构建失败但旧产物还在”被误判为成功；`Assets\StreamingAssets` 先清再拷，防止上个项目素材残留。
  - 失败抛 `UnityBuildError`（`RuntimeError` 子类），消息 = `[Unity 步骤] 原因 + 日志路径 + Unity 日志最后 40 行`；属性 `step / log / log_tail`。
- `app\engine\export_unity.py`：新增 `export_unity_and_build`（见下），内部 = `export_unity()` + `build_exe()`，没有第二份构建逻辑。原 `export_unity()` 未改。
- `app\engine\export_mock.py`：`export_web(root, graph, preset=None)`
  - `index.html` 改为用 `app.ui_presets.render(preset, 'title', ...)` 渲染的标题页（带 `<!-- ui_preset: <id> -->` 标记），「开始/继续」链接跳 `play.html`。
  - 原来的网页播放器原样挪到 `play.html`。
  - 选预设：导参 `preset` > `graph.style` > `project.ui_preset` > `project.style`（可以是 id，也可以是关键词，走 `ui_presets.match`）> 默认第一套 `ink-classic`。
  - 背景取第一个节点的 bg，立绘取前两个不同 pose 作为 char_left/right。
- 改动前的原文件备份在 `tools\_grok_patch\*.orig.py`；补丁脚本与验证脚本也在 `tools\_grok_patch\`。

## 函数签名
```python
def export_unity_and_build(root, graph, *, job_dir=None, timeout=1800,
                           unity_project=None, unity_exe=None, echo=None) -> dict
```
成功返回：
```json
{"ok": true, "out": "D:\\UnityProject\\Build\\奇迹工坊.exe", "unity_project": "D:\\UnityProject",
 "build_log": "…Unity 日志最后 40 行…", "log_file": "…\\tools\\unity_logs\\build.log",
 "seconds": 17.0, "exe_bytes": 671744, "build_dir_bytes": 89224214,
 "export": {"nodes": 40, "images": 3, "voices": 17, "scripts": ["Builder.cs","GalgameFlow.cs","GameData.cs"]}}
```
失败：graph 没 nodes → `ValueError`；Unity 建工程/构建失败或超时 → `RuntimeError`（`UnityBuildError`），消息含 Unity 日志尾部。`job_dir` = Unity 日志目录（默认 `tools\unity_logs`）。

## 验证结果（真跑）
| 命令 | 结果 |
|---|---|
| `export_unity_and_build(projects\p_63b2b758e0ab46d1bf4201629468eb36, graph)` | ok=True，**耗时 17.0 秒**（导出 + 构建；工程已存在，跳过建工程） |
| `Test-Path D:\UnityProject\Build\奇迹工坊.exe` | **True**（mtime 2026-11-07 21:03:34） |
| exe 文件大小 | **671,744 字节**（exe 本体只是启动器） |
| Build 目录总大小 | **89,224,214 字节 ≈ 85.1 MiB / 89.2 MB**（任务书说的“约 89 MB”是这个） |
| 启动 exe | 窗口标题「奇迹工坊」，全屏窗口，显示「起点 / 故事从这里开始 / 继续」；截图 `docs\screens\grok\unity_exe.png`；截完已关闭进程 |
| `export_web` × 5（默认 + 四套预设） | `index.html` 标记分别为 ink-classic(默认)/ink-classic/cinema-dark/paper-indie/theater-cold，`play.html` 存在，链接指向 play.html |
| `tests\check_contract.py` | 全 PASS |
| `tests\test_m1.py` | Ran 6 tests OK |

Unity 日志（`tools\unity_logs\build.log`）里成功的几行：
```
Build Finished, Result: Success.
BUILD_OK 输出=D:\UnityProject\Build\奇迹工坊.exe 大小=89224214
```
进程退出码 0。

## 给经理的接线说明（`app\mock_api.py` / `export_game`，我没改）
`export_game` 在 `export_mock.py` 里，但按任务书把 unity 分派交给你接。建议：
```python
# export_game(root, graph, format) 里，或 mock_api 的导出路由里：
if format == 'unity':
    from .export_unity import export_unity_and_build   # mock_api 里写 from app.engine.export_unity import ...
    return export_unity_and_build(root, graph, timeout=1800)   # 返回 dict，out 是 exe 路径
if format == 'renpy':
    return export_renpy(root, graph)
return export_web(root, graph)
```
- 这个调用会阻塞几十秒到十几分钟（首次建工程 3~10 分钟），**放后台任务/线程**，别在请求线程里同步等。
- 捕获 `RuntimeError` 把 `str(exc)` 原样回给前端（已含 Unity 日志尾部）；`ValueError` 当 422。
- 返回值是 dict 不是 Path，路由里取 `result['out']`。
- 同一时间只能跑一个 Unity 构建（同一个 D:\UnityProject），需要加锁或排队。

## 发现但没改的（交给经理转）
- 现象：用 `app\.venv\Scripts\python.exe` 跑时，**写 D:\UnityProject 报 PermissionError(13)**（它起的子进程也一样），而系统 Python `C:\Users\q3153\AppData\Local\Programs\Python\Python311\python.exe`、PowerShell、cmd 都能写。ACL 正常（Authenticated Users: Modify），Defender 服务不在跑。看起来是 venv 的 python 被某个沙箱/安全策略按路径限制了。
- 本次真构建是用系统 Python 跑的（`export_unity_and_build` 只依赖标准库）。**后端服务如果用 .venv 起，点导出 Unity 会在“复制脚本”那一步报 PermissionError。**
- 建议：查清 .venv\python.exe 的写权限限制（安全软件白名单），或者把 Unity 工程挪到工作区内并改 `PROJECT` 常量。
- 为什么我没改：属于环境/安全配置，不在我的文件边界内。

## 给下一位的注意事项
- 磁盘：Build 目录约 89 MB，保留着作为交付物；Library 没动（删了下次要重新导入，几分钟）。临时 zip 已删。
- 截图要用 `AttachThreadInput` + 模拟 Alt 再 `SetForegroundWindow`，否则 Unity 窗口拿不到前台，截到的是别的窗口（踩过一次）。
- PowerShell 命令里有中文路径会偶发 AMSI 崩溃，脚本一律写成 .py 文件跑。
