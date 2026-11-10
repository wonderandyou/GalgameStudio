# 批量截界面预设：ui\presets\*.html -> ui\presets\_shots\*.png
# 关键：Edge 必须用 pwsh 直接起（python subprocess 会因管道死锁卡住）
# 注意：不要设 $ErrorActionPreference='Stop' —— Edge 会往 stderr 打大量无关警告
# （QQBrowser 路径、crashpad 等），Stop 会把它们当终止错误中断整个循环。
$ErrorActionPreference = 'Continue'
$root = 'D:\软件\GalgameStudio'
$presetDir = Join-Path $root 'ui\presets'
$shotDir   = Join-Path $presetDir '_shots'
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$profRoot = Join-Path $presetDir '_prof'

New-Item -ItemType Directory -Force -Path $shotDir | Out-Null

$pages = Get-ChildItem $presetDir -Filter '*.html' | Sort-Object Name
if (-not $pages) { Write-Output '没有 HTML 可截'; exit 1 }

$n = 0
foreach ($page in $pages) {
    $name = [System.IO.Path]::GetFileNameWithoutExtension($page.Name)
    $out  = Join-Path $shotDir "$name.png"
    $prof = Join-Path $profRoot $name
    Remove-Item $prof -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $out -Force -ErrorAction SilentlyContinue
    $url = 'file:///' + ($page.FullName -replace '\\','/')
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $edge --headless=new --disable-gpu --no-first-run --no-default-browser-check `
        --hide-scrollbars --force-device-scale-factor=1.5 --virtual-time-budget=3500 `
        --window-size=1600,900 --user-data-dir="$prof" --screenshot="$out" $url 2>&1 |
        Select-String 'written to file' | Out-Null
    $sw.Stop()
    if (Test-Path $out) {
        $kb = [int]((Get-Item $out).Length / 1KB)
        Write-Output ("{0,-28} {1,5} KB  {2,3}s" -f $name, $kb, [int]$sw.Elapsed.TotalSeconds)
        $n++
    } else {
        Write-Output ("{0,-28} FAIL" -f $name)
    }
}
Write-Output "== 共截图 $n / $($pages.Count) =="
