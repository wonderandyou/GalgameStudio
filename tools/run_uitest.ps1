# 用无头 Edge 跑 UI 自测，把结果写在 <title> 里抓回来
# 用法: pwsh -File run_uitest.ps1 -Mode uitest
param(
  [string]$Mode = 'uitest',
  [int]$Budget = 8000
)

$ui  = 'D:\软件\GalgameStudio\ui'
$ts  = Get-Date -Format 'yyyyMMddHHmmss'

# 每次跑都刷新缓存戳，避免浏览器读到旧文件
$idx = Join-Path $ui 'index.html'
$t = [System.IO.File]::ReadAllText($idx, [System.Text.Encoding]::UTF8)
$t = [regex]::Replace($t, '\?v=\d{14}', "?v=$ts")
[System.IO.File]::WriteAllText($idx, $t, (New-Object System.Text.UTF8Encoding($false)))

$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$url  = "file:///D:/%E8%BD%AF%E4%BB%B6/GalgameStudio/ui/index.html?$Mode=1&view=editor&t=$ts"
$tmp  = Join-Path $env:TEMP "gal_$Mode.html"
$err  = Join-Path $env:TEMP "gal_$Mode.err.txt"
if (Test-Path $tmp) { Remove-Item $tmp -Force }

$a = @('--headless=new','--disable-gpu','--no-sandbox',
       "--virtual-time-budget=$Budget",'--window-size=1600,1000','--dump-dom',$url)
$pr = Start-Process -FilePath $edge -ArgumentList $a -NoNewWindow -Wait -PassThru `
      -RedirectStandardOutput $tmp -RedirectStandardError $err

if (-not (Test-Path $tmp)) { Write-Output "FAIL: no output (exit=$($pr.ExitCode))"; exit 1 }
$out = [System.IO.File]::ReadAllText($tmp, [System.Text.Encoding]::UTF8)
$m = [regex]::Match($out, '<title>(.*?)</title>', 'Singleline')
if ($m.Success) {
  Write-Output $m.Groups[1].Value
} else {
  Write-Output "NO TITLE (exit=$($pr.ExitCode), bytes=$($out.Length))"
  $e = [regex]::Match($out, 'ERR [^<]{0,200}')
  if ($e.Success) { Write-Output ("PAGE-ERR: " + $e.Value) }
}
