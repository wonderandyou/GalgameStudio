# 启动 mock 后端（演示用，端口 8791）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\run-mock.ps1
#
# 它和 app/ 下 GPT 写的真后端完全独立、互不干扰：
#   - 真后端：8790，只实现了 M1（配置读写、连接探测）
#   - mock  ：8791，把 M2 的生成/SSE/导出全实现了，导出会真的写文件
#
# 前端连它：ui/index.html?mode=real&base=8791
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

Write-Host "mock 后端启动：http://127.0.0.1:8791/api/health"
Write-Host "前端连法：ui/index.html?mode=real&base=8791"
Write-Host "导出目录：$root\out"

Set-Location "$root\tools"
& "$root\app\.venv\Scripts\python.exe" -m uvicorn mock_server:app --host 127.0.0.1 --port 8791
