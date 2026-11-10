param(
    [string]$Python = 'D:\软件\GalgameStudio\app\.venv\Scripts\python.exe',
    [int]$Port = 8790,
    [string]$TestRoot = (Join-Path $env:TEMP ('GalgameStudio-M2-' + [Guid]::NewGuid().ToString('N')))
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null
$saved = @{}
foreach ($name in @('GAL_ROOT','GAL_STATE_DIR','GAL_MOCK','GAL_PORT','GAL_MOCK_STEP_DELAY')) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$process = $null
$base = "http://127.0.0.1:$Port"
function PostJson($url, $data) {
    $json = $data | ConvertTo-Json -Depth 12 -Compress
    Invoke-RestMethod -Uri $url -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($json))
}
try {
    $env:GAL_ROOT = $TestRoot
    $env:GAL_STATE_DIR = Join-Path $TestRoot 'state'
    $env:GAL_MOCK = '1'
    $env:GAL_PORT = "$Port"
    $env:GAL_MOCK_STEP_DELAY = '0.2'
    & $Python -c 'import ssl, fastapi, uvicorn, httpx' 2>&1 | Tee-Object -FilePath (Join-Path $TestRoot 'preflight.txt')
    if ($LASTEXITCODE -ne 0) { throw '运行时检查失败，见 preflight.txt；未启动服务。' }
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    try { $listener.Start() } finally { $listener.Stop() }
    $process = Start-Process -FilePath $Python -ArgumentList '-m','app.main' -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $TestRoot 'server.stdout.txt') -RedirectStandardError (Join-Path $TestRoot 'server.stderr.txt')
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        if ($process.HasExited) { throw '后端启动失败，见 server.stderr.txt' }
        try { $health = Invoke-RestMethod "$base/api/health"; $ready = $true; break } catch { Start-Sleep -Milliseconds 200 }
    }
    if (-not $ready -or -not $health.mock) { throw 'Mock 健康检查失败' }
    $health | ConvertTo-Json -Compress
    $project = PostJson "$base/api/projects" @{name='M2 实测'}
    $job = PostJson "$base/api/projects/$($project.id)/generate" @{requirement='雨夜天台';params=@{node_count=8;branch_depth=2;auto_voice=$true}}
    "curl.exe -N $base$($job.sse)"
    & curl.exe -N --fail --silent --show-error --max-time 90 "$base$($job.sse)" | Tee-Object -FilePath (Join-Path $TestRoot 'sse.txt')
    if ($LASTEXITCODE -ne 0) { throw 'curl SSE 请求失败' }
    $sse = Get-Content (Join-Path $TestRoot 'sse.txt') -Raw
    if (-not $sse.Contains('event: done') -or -not $sse.Contains('event: node') -or -not $sse.Contains(': keepalive')) { throw 'SSE 缺少节点/终态/心跳' }
    if ($sse.Contains('event: error')) { throw '生成发生错误，见 sse.txt' }
    $graph = Invoke-RestMethod "$base/api/projects/$($project.id)/graph"
    $graph | ConvertTo-Json -Depth 25 | Set-Content (Join-Path $TestRoot 'graph-response.json') -Encoding utf8
    & $Python -m app.engine.graph --check (Join-Path $project.path 'graph.json')
    if ($LASTEXITCODE -ne 0) { throw 'graph 校验失败' }
    foreach ($format in @('renpy','web')) {
        $export = PostJson "$base/api/projects/$($project.id)/export" @{format=$format}
        & curl.exe -N --fail --silent --show-error --max-time 30 "$base/api/jobs/$($export.job_id)/events" | Tee-Object -FilePath (Join-Path $TestRoot "export-$format.txt")
        if ($LASTEXITCODE -ne 0) { throw '导出 SSE 请求失败' }
        $result = Get-Content (Join-Path $TestRoot "export-$format.txt") -Raw
        if (-not $result.Contains('event: done') -or $result.Contains('event: error')) { throw '导出失败' }
        "Export: $($export.out)"
    }
    $cancelJob = PostJson "$base/api/projects/$($project.id)/generate" @{requirement='取消测试'}
    PostJson "$base/api/jobs/$($cancelJob.job_id)/cancel" @{} | Out-Null
    & curl.exe -N --fail --silent --show-error --max-time 15 "$base$($cancelJob.sse)" | Tee-Object -FilePath (Join-Path $TestRoot 'cancel-sse.txt')
    if ($LASTEXITCODE -ne 0) { throw '取消流请求失败' }
    if (-not (Get-Content (Join-Path $TestRoot 'cancel-sse.txt') -Raw).Contains('JOB_CANCELLED')) { throw '缺少取消事件' }
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw '单元/集成测试失败' }
    'M2_SMOKE_PASSED'
} finally {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id }
    foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    "验收文件目录：$TestRoot"
}

