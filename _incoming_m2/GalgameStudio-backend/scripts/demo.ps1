param([string]$Python = 'D:\软件\GalgameStudio\app\.venv\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath $Python)) { throw "找不到已有解释器：$Python" }
& $Python -c 'import ssl, fastapi, uvicorn, httpx'
if ($LASTEXITCODE -ne 0) { throw '解释器运行检查失败；未修改环境。' }
$previousState = $env:GAL_STATE_DIR
$previousMock = $env:GAL_MOCK
$env:GAL_STATE_DIR = Join-Path (Get-Location) '.demo-state'
$env:GAL_MOCK = '0'
$port = if ($env:GAL_PORT) { [int]$env:GAL_PORT } else { 8790 }
$base = "http://127.0.0.1:$port"
$process = $null
try {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
    try { $listener.Start() } finally { $listener.Stop() }
    $process = Start-Process -FilePath $Python -ArgumentList '-m','app.main' -WindowStyle Hidden -PassThru
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        if ($process.HasExited) { throw "后端已退出：$($process.ExitCode)" }
        try { $health = Invoke-RestMethod "$base/api/health"; $ready = $true; break } catch { Start-Sleep -Milliseconds 250 }
    }
    if (-not $ready) { throw '服务启动失败' }
    'GET /api/health'; $health | ConvertTo-Json -Compress
    if (-not $health.ok -or $health.mock) { throw '健康检查不符合 M1 要求' }
    $body = @{comfyui=@{base_url='http://127.0.0.1:8188'}} | ConvertTo-Json
    Invoke-RestMethod -Method Put "$base/api/providers" -ContentType 'application/json' -Body $body | Out-Null
    'POST /api/providers/comfyui/test'
    Invoke-RestMethod -Method Post "$base/api/providers/comfyui/test" | ConvertTo-Json -Depth 6 -Compress
    $body = @{comfyui=@{base_url='http://127.0.0.1:1'}} | ConvertTo-Json
    Invoke-RestMethod -Method Put "$base/api/providers" -ContentType 'application/json' -Body $body | Out-Null
    '故意错误地址 http://127.0.0.1:1'
    $bad = Invoke-RestMethod -Method Post "$base/api/providers/comfyui/test"
    $bad | ConvertTo-Json -Depth 6 -Compress
    if ($bad.ok) { throw '验收失败：错误地址被误判成功' }
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw '自动测试未通过' }
} finally {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id }
    $env:GAL_STATE_DIR = $previousState
    $env:GAL_MOCK = $previousMock
}


