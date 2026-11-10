# 跑 GalgameStudio 前端 e2e：起临时 http 服务 -> 无头浏览器跑用例 -> 抓 title -> 关服务
# 用法（PowerShell 5.1 下建议直接 . 执行或内联）：
#   .\run_e2e.ps1 -Case baseline
#   .\run_e2e.ps1 -Case views -Dpr 1.5
param(
  [Parameter(Mandatory = $true)][string]$Case,
  [double]$Dpr = 1,
  [int]$WindowW = 1600,
  [int]$WindowH = 1000,
  [int]$Budget = 9000,
  [switch]$KeepServer
)

$ErrorActionPreference = 'Stop'
$ui = 'D:\软件\GalgameStudio\ui'
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'

# 1) 刷新缓存戳（app.js / e2e.js 每次改动都要变）
$ts = Get-Date -Format 'yyyyMMddHHmmss'
$idx = Join-Path $ui 'index.html'
$t = [System.IO.File]::ReadAllText($idx, [System.Text.Encoding]::UTF8)
$t = [regex]::Replace($t, '\?v=\d{14}', "?v=$ts")
[System.IO.File]::WriteAllText($idx, $t, (New-Object System.Text.UTF8Encoding($false)))

# 2) 确保 8799 有服务
$listen = Get-NetTCPConnection -LocalPort 8799 -State Listen -ErrorAction SilentlyContinue
$startedHere = $false
if (-not $listen) {
  $srv = Start-Process -FilePath 'python' -ArgumentList @('-m','http.server','8799','--bind','127.0.0.1') `
         -WorkingDirectory $ui -PassThru -WindowStyle Hidden
  $startedHere = $true
  Start-Sleep -Seconds 2
  Write-Host ("[server] started pid=" + $srv.Id) -ForegroundColor DarkGray
}

try {
  # 3) 跑用例
  $url = "http://127.0.0.1:8799/index.html?e2e=$Case&t=$ts"
  $tmp = Join-Path $env:TEMP "e2e_$Case.html"
  $err = Join-Path $env:TEMP "e2e_$Case.err"
  if (Test-Path $tmp) { Remove-Item $tmp -Force }

  $args = @(
    '--headless=new','--disable-gpu','--no-sandbox','--hide-scrollbars',
    "--force-device-scale-factor=$Dpr",
    "--virtual-time-budget=$Budget",
    "--window-size=$WindowW,$WindowH",
    '--dump-dom', $url
  )
  Start-Process -FilePath $edge -ArgumentList $args -NoNewWindow -Wait `
    -RedirectStandardOutput $tmp -RedirectStandardError $err | Out-Null

  $out = [System.IO.File]::ReadAllText($tmp, [System.Text.Encoding]::UTF8)
  $m = [regex]::Match($out, '<title>(.*?)</title>', 'Singleline')
  if (-not $m.Success) {
    Write-Host "NO TITLE (see $err)" -ForegroundColor Red
    exit 1
  }

  $raw = $m.Groups[1].Value
  if ($raw -notmatch '^E2E ') {
    Write-Host "NOT AN E2E RESULT: $raw" -ForegroundColor Red
    exit 1
  }

  # 4) 解析并打印成可读表
  $json = $raw.Substring(4) | ConvertFrom-Json
  Write-Host ""
  Write-Host ("=== E2E [{0}]  {1}  ({2} 项, 失败 {3}) ===" -f `
    $json.case, $(if ($json.pass) { 'PASS' } else { 'FAIL' }), $json.total, $json.failed) `
    -ForegroundColor $(if ($json.pass) { 'Green' } else { 'Red' })

  foreach ($i in $json.items) {
    $mark = if ($i.ok) { '  OK  ' } else { ' FAIL ' }
    $color = if ($i.ok) { 'DarkGray' } else { 'Red' }
    Write-Host ("{0} {1,-34} {2}" -f $mark, $i.k, $i.n) -ForegroundColor $color
  }
  if ($json.errors -and $json.errors.Count -gt 0) {
    Write-Host "--- JS 错误 ---" -ForegroundColor Yellow
    $json.errors | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
  }
  if (-not $json.pass) { exit 1 }
}
finally {
  if ($startedHere -and -not $KeepServer) {
    $c = Get-NetTCPConnection -LocalPort 8799 -State Listen -ErrorAction SilentlyContinue
    if ($c) {
      Stop-Process -Id (($c | Select-Object -First 1).OwningProcess) -Force -ErrorAction SilentlyContinue
      Write-Host "[server] stopped" -ForegroundColor DarkGray
    }
  }
}
