param([string]$Python = 'D:\软件\GalgameStudio\app\.venv\Scripts\python.exe', [switch]$Mock, [string]$StateDir = '')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath $Python)) { throw "找不到已有解释器：$Python" }
# 使用已经安装好的环境，不安装依赖、不新建 venv。
& $Python -c 'import ssl, fastapi, uvicorn, httpx; print("Environment OK")'
if ($LASTEXITCODE -ne 0) { throw '解释器运行检查失败，见上方真实错误。未修改环境。' }
$env:GAL_MOCK = if ($Mock) { '1' } else { '0' }
if ($StateDir) { $env:GAL_STATE_DIR = $StateDir }
& $Python -m app.main
if ($LASTEXITCODE -ne 0) { throw "后端退出：$LASTEXITCODE" }



