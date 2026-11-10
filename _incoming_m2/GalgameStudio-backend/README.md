# 鲸落工坊后端 · P0 修复 + M2 Mock

本包实现 P0-1/2/3 修复与 M2 mock 全链路代码。**尚未通过真实 HTTP/SSE 运行验收**，原因及实际输出见 docs/verification.md；不把静态检查当作 curl 验收。

## 启动

解压后进入本包根目录，在 PowerShell 执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Mock
```

使用现有解释器 `D:\软件\GalgameStudio\app\.venv\Scripts\python.exe`，不安装依赖。等价于设置 `GAL_MOCK=1` 后从本包根目录运行 `-m app.main`。默认端口 8790。

若默认凭据目录无法写入，可显式选择一个你确认可写、且位于项目目录之外的位置：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Mock -StateDir 'D:\GalgameStudioState'
```

脚本不会静默回落目录。权限错误返回 STATE_NOT_WRITABLE、实际路径和原始错误，并带 CORS 响应头。

## 本版内容

- 外层 CORS 使用 `allow_origins=['*']`，不启用 Cookie credentials；未处理异常的 500 也经过 CORS。
- 配置校验错误带 `error.field`；批量校验仍保持原子性。
- 创建项目、启动生成、SSE、读取 graph、异步导出五个接口。
- 补充项目列表、取消任务、PNG/WAV 资源接口。
- agent/node/log/done/error 事件保持既定协议；每 5 秒 keepalive；逐节点写图和发送 patch。
- JSON 任务队列落盘，支持事件回放、Last-Event-ID 与重启后按阶段恢复。
- mock 不调用模型：纯色 PNG、一秒 24kHz WAV；auto_voice=false 时 voice=null。
- graph 共用 Pydantic 契约及完整图约束检查。
- renpy/web 导出占位素材工程；video 返回明确的 501，不返回假导出路径。

Mock 台词固定为四字文本以配合一秒语音，需求文本会随任务保存，不做语义生成。节点数 4–64；branch_depth 0–8，受节点数限制自动收敛到可实现深度。resolution 保存为目标分辨率，mock 图片固定小尺寸，便于快速联调。consistency 在 mock 中通过复用同一参考图和立绘实现。

不带 -Mock 的模式不进行假生成，而是返回 REAL_GENERATION_NOT_IMPLEMENTED。M3 真模型生成、单节点重生成、M5 SDK 试玩验收和录像仍不属于本版已完成功能。

## 自测入口

```powershell
powershell -ExecutionPolicy Bypass -File scripts\m2_smoke.ps1
```

脚本独立选择测试目录，启动本包服务，用真正的 `curl.exe -N` 接收事件，验证节点/心跳/终态，读取并校验 graph，导出网页与 Ren'Py，再取消一项任务验证 error 事件，最后运行回归测试。每步失败即停止，全部通过才打印 M2_SMOKE_PASSED。测试数据和原始输出保留在脚本最后打印的目录，不覆盖日常项目/凭据。请先关闭占用 8790 的其他后端。

独立图契约验证：

```powershell
$env:PYTHONPATH = (Get-Location).Path
& 'D:\软件\GalgameStudio\app\.venv\Scripts\python.exe' tests\check_contract.py
```

## 交付状态

已实际通过：19 个 Python 文件与 3 个 PowerShell 脚本语法检查；1,098 组图契约组合；环/冗余边拒绝；PNG CRC/像素缓冲；WAV 格式与时长。

未通过：当前工具环境的 _ssl DLL 加载和 Python 写文件受阻，HTTP/SSE 测试尚不能运行，未取得 curl -N 的真实事件流。完整原始失败日志随包交付。没有修改 D 盘原项目或 ui 文件。
