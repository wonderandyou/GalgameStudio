# 启动后端（M2）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\run-backend.ps1
#
# 说明 1：后端默认把凭据写在 %LOCALAPPDATA%\GalgameStudio，
# 这台机器上该路径"能建目录、不能写文件"（WinError 5 / Errno 13）。
# 所以这里用 GAL_STATE_DIR 把它指到项目内的 .state，避开权限问题。
#
# 说明 2：M2 交付里 generate/export 只有 mock 实现（GAL_MOCK=1），
# 不开就是 501 REAL_GENERATION_NOT_IMPLEMENTED。默认开 1，
# 需要跑真实模型时用 $env:GAL_MOCK='0' 覆盖。
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

$env:GAL_STATE_DIR = Join-Path $root '.state'
$env:GAL_PORT = if ($env:GAL_PORT) { $env:GAL_PORT } else { '8790' }
$env:GAL_MOCK = if ($env:GAL_MOCK) { $env:GAL_MOCK } else { '1' }

Write-Host "GAL_STATE_DIR = $env:GAL_STATE_DIR"
Write-Host "GAL_PORT      = $env:GAL_PORT"
Write-Host "GAL_MOCK      = $env:GAL_MOCK"
Write-Host "启动：http://127.0.0.1:$($env:GAL_PORT)/api/health"

Set-Location $root
& "$root\app\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $env:GAL_PORT
