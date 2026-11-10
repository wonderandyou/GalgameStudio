# 端到端联调验收（前端契约视角）
# 用法：powershell -ExecutionPolicy Bypass -File tools\e2e_test.ps1
#       powershell -ExecutionPolicy Bypass -File tools\e2e_test.ps1 -Base http://127.0.0.1:8790
#
# 设计原则：
#   - 只测「前端真的会调」的那些接口，字段照 提示词-后端.md §3
#   - 接口不存在（404）算 SKIP，不算 FAIL —— M1 阶段只有 health/providers，属正常
#   - 任何一项 FAIL 就退出码 1，方便以后接 CI

param(
  [string]$Base = "http://127.0.0.1:8790"
)

$ErrorActionPreference = 'Stop'
$script:pass = 0
$script:fail = 0
$script:skip = 0
$script:lines = @()

function Record($state, $name, $detail) {
  $script:lines += ("[{0}] {1,-38} {2}" -f $state, $name, $detail)
  switch ($state) {
    'PASS' { $script:pass++ }
    'FAIL' { $script:fail++ }
    'SKIP' { $script:skip++ }
  }
}

function TryApi($method, $path, $body) {
  # 用 HttpWebRequest + UTF-8 StreamReader：
  # Invoke-WebRequest 在 Windows PowerShell 下按 ISO-8859-1 解响应，
  # 中文路径会变成 "D:\è½¯ä»¶\..."，导致后续 Test-Path 全部失败。
  $uri = "$Base$path"
  $req = [System.Net.HttpWebRequest]::Create($uri)
  $req.Method = $method
  $req.Timeout = 20000
  $req.ContentType = 'application/json; charset=utf-8'
  if ($body) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    $req.ContentLength = $bytes.Length
    $st = $req.GetRequestStream()
    $st.Write($bytes, 0, $bytes.Length)
    $st.Close()
  }
  try {
    $resp = $req.GetResponse()
    $code = [int]$resp.StatusCode
    $sr = New-Object System.IO.StreamReader($resp.GetResponseStream(), [System.Text.Encoding]::UTF8)
    $text = $sr.ReadToEnd()
    $sr.Close(); $resp.Close()
    return @{ ok = $true; status = $code; body = $text }
  } catch [System.Net.WebException] {
    $r = $_.Exception.Response
    $code = if ($r) { [int]$r.StatusCode } else { 0 }
    $text = ''
    if ($r) {
      $sr = New-Object System.IO.StreamReader($r.GetResponseStream(), [System.Text.Encoding]::UTF8)
      $text = $sr.ReadToEnd()
      $sr.Close(); $r.Close()
    }
    return @{ ok = $false; status = $code; body = $text }
  } catch {
    return @{ ok = $false; status = 0; body = $_.Exception.Message }
  }
}

Write-Host ""
Write-Host "目标: $Base" -ForegroundColor Cyan
Write-Host ("-" * 66)

# ---------- 1. health ----------
$h = TryApi 'GET' '/api/health'
if ($h.ok) {
  $j = $h.body | ConvertFrom-Json
  if ($j.ok) { Record 'PASS' 'GET /api/health' ("v$($j.version) mock=$($j.mock)") }
  else { Record 'FAIL' 'GET /api/health' 'ok != true' }
} else {
  Record 'FAIL' 'GET /api/health' "HTTP $($h.status) —— 服务没起来？"
  Write-Host ""
  Write-Host "后端没起来，后面全部跳过。" -ForegroundColor Red
  $script:lines | ForEach-Object { Write-Host $_ }
  exit 1
}

# ---------- 2. providers ----------
$p = TryApi 'GET' '/api/providers'
if ($p.ok) {
  $cfg = $p.body | ConvertFrom-Json
  $keys = @($cfg.PSObject.Properties.Name)
  $need = @('qwen_image', 'bailian_tts', 'llm', 'comfyui', 'local_tts', 'local_llm')
  $missing = @($need | Where-Object { $keys -notcontains $_ })
  if ($missing.Count -eq 0) { Record 'PASS' 'GET /api/providers' "6 个 provider 齐全" }
  else { Record 'FAIL' 'GET /api/providers' "缺: $($missing -join ',')" }

  $leak = @($keys | Where-Object { $cfg.$_ -and $cfg.$_.api_key -and $cfg.$_.api_key -ne '***' })
  if ($leak.Count -eq 0) { Record 'PASS' 'providers 密钥脱敏' 'api_key 都是 ***' }
  else { Record 'FAIL' 'providers 密钥脱敏' "明文泄露: $($leak -join ',')" }
} else {
  Record 'FAIL' 'GET /api/providers' "HTTP $($p.status)"
}

# ---------- 3. test 探测（离线服务应返回 ok:false 但结构正确） ----------
$t = TryApi 'POST' '/api/providers/comfyui/test'
if ($t.ok) {
  $tj = $t.body | ConvertFrom-Json
  if ($null -ne $tj.ok) { Record 'PASS' 'POST /providers/comfyui/test' "ok=$($tj.ok)（离线应为 false）" }
  else { Record 'FAIL' 'POST /providers/comfyui/test' '缺 ok 字段' }
} elseif ($t.status -eq 404) {
  Record 'SKIP' 'POST /providers/comfyui/test' '接口不存在'
} else {
  Record 'FAIL' 'POST /providers/comfyui/test' "HTTP $($t.status)"
}

# ---------- 4. PUT providers（前端白名单形状） ----------
$body = '{"comfyui":{"enabled":true,"base_url":"http://127.0.0.1:8188","workflow_bg":"galgame_bg.json"}}'
$put = TryApi 'PUT' '/api/providers' $body
if ($put.ok) { Record 'PASS' 'PUT /api/providers' '写入成功' }
else { Record 'FAIL' 'PUT /api/providers' "HTTP $($put.status) $($put.body)" }

# ---------- 5. 项目 ----------
$proj = TryApi 'POST' '/api/projects' '{"name":"e2e-验收项目"}'
$projectId = $null
if ($proj.ok) {
  $pj = $proj.body | ConvertFrom-Json
  $projectId = $pj.id
  if ($projectId) { Record 'PASS' 'POST /api/projects' "id=$projectId" }
  else { Record 'FAIL' 'POST /api/projects' '没返回 id' }
} elseif ($proj.status -eq 404) {
  Record 'SKIP' 'POST /api/projects' '接口不存在（M2）'
} else {
  Record 'FAIL' 'POST /api/projects' "HTTP $($proj.status)"
}

# ---------- 6. 生成 ----------
$jobId = $null
if ($projectId) {
  $genBody = '{"requirement":"雨夜天台，两个人，两张车票，两个结局。","params":{"node_count":5,"branch_depth":2,"auto_voice":true,"consistency":true,"resolution":[1920,1080]}}'
  $g = TryApi 'POST' "/api/projects/$projectId/generate" $genBody
  if ($g.ok) {
    $gj = $g.body | ConvertFrom-Json
    $jobId = $gj.job_id
    if ($jobId) { Record 'PASS' 'POST .../generate' "job=$jobId" }
    else { Record 'FAIL' 'POST .../generate' '没返回 job_id' }
  } else {
    Record 'FAIL' 'POST .../generate' "HTTP $($g.status) $($g.body)"
  }
} else {
  Record 'SKIP' 'POST .../generate' '没有项目 id'
}

# ---------- 7. SSE ----------
if ($jobId) {
  try {
    $raw = & curl.exe -s -N --max-time 25 "$Base/api/jobs/$jobId/events" 2>$null
    $text = ($raw | Out-String)
    $hasAgent = $text -match 'event:\s*agent'
    $hasDone = $text -match 'event:\s*done'
    $hasLog = $text -match 'event:\s*log'
    if ($hasAgent -and $hasDone) {
      Record 'PASS' 'GET /api/jobs/{id}/events' "agent+log+done 齐全（$([regex]::Matches($text,'event:\s*\w+').Count) 个事件）"
    } elseif ($hasAgent) {
      Record 'FAIL' 'GET /api/jobs/{id}/events' "收到 agent 但没有 done（流没结束？）"
    } else {
      Record 'FAIL' 'GET /api/jobs/{id}/events' "没收到 agent 事件，前 200 字：$($text.Substring(0,[Math]::Min(200,$text.Length)))"
    }
  } catch {
    Record 'FAIL' 'GET /api/jobs/{id}/events' "curl 失败：$($_.Exception.Message)"
  }
} else {
  Record 'SKIP' 'GET /api/jobs/{id}/events' '没有 job_id'
}

# ---------- 8. graph ----------
if ($projectId) {
  $gr = TryApi 'GET' "/api/projects/$projectId/graph"
  if ($gr.ok) {
    $gj = $gr.body | ConvertFrom-Json
    $nodes = @($gj.nodes)
    $null2 = $null
    if ($nodes.Count -gt 0) {
      $types = @($nodes | ForEach-Object { $_.type } | Sort-Object -Unique)
      $badType = @($types | Where-Object { $_ -notin @('start', 'scene', 'choice', 'ending') })
      if ($badType.Count -gt 0) { Record 'FAIL' 'GET .../graph 节点类型' ($badType -join ',') }
      else { Record 'PASS' 'GET .../graph' ("$($nodes.Count) 节点 / " + @($gj.edges).Count + " 连线 / 类型 " + ($types -join ' ')) }

      # choice 必须有 choices，ending 必须有 end_kind
      $badChoice = @($nodes | Where-Object { $_.type -eq 'choice' -and -not $_.choices })
      $badEnd = @($nodes | Where-Object { $_.type -eq 'ending' -and -not $_.end_kind })
      if ($badChoice.Count -eq 0 -and $badEnd.Count -eq 0) { Record 'PASS' 'graph 结构约束' 'choice 有 choices / ending 有 end_kind' }
      else { Record 'FAIL' 'graph 结构约束' "choice 缺 choices=$($badChoice.Count) ending 缺 end_kind=$($badEnd.Count)" }
    } else {
      Record 'FAIL' 'GET .../graph' 'nodes 为空'
    }
  } else {
    Record 'FAIL' 'GET .../graph' "HTTP $($gr.status)"
  }
} else {
  Record 'SKIP' 'GET .../graph' '没有项目 id'
}

# ---------- 9. export ----------
if ($projectId) {
  $ex = TryApi 'POST' "/api/projects/$projectId/export" '{"format":"renpy"}'
  if ($ex.ok) {
    $ej = $ex.body | ConvertFrom-Json
    if ($ej.out -or $ej.job_id) { Record 'PASS' 'POST .../export' "out=$($ej.out) job=$($ej.job_id)" }
    else { Record 'FAIL' 'POST .../export' '没返回 out/job_id' }

    # 导出产物落地了就顺手做一次 Ren'Py 工程自检（jump 闭环 / 资源存在 / 缩进）
    if ($ej.out -and (Test-Path $ej.out)) {
      $pyExe = Join-Path (Split-Path $PSScriptRoot -Parent) "app\.venv\Scripts\python.exe"
      $rpyTool = Join-Path $PSScriptRoot "verify_rpy.py"
      if ((Test-Path $pyExe) -and (Test-Path $rpyTool)) {
        $v = & $pyExe $rpyTool $ej.out 2>&1
        if ($LASTEXITCODE -eq 0) { Record 'PASS' 'RenPy 工程自检' 'label 闭环 / 资源齐全 / 缩进正确' }
        else { Record 'FAIL' 'RenPy 工程自检' (($v | Where-Object { $_ -match 'FAIL' }) -join ' | ') }
      } else {
        Record 'SKIP' 'RenPy 工程自检' '解释器或 verify_rpy.py 不在'
      }
    }
  } elseif ($ex.status -eq 404) {
    Record 'SKIP' 'POST .../export' '接口不存在（M5）'
  } else {
    Record 'FAIL' 'POST .../export' "HTTP $($ex.status) $($ex.body)"
  }
} else {
  Record 'SKIP' 'POST .../export' '没有项目 id'
}

# ---------- 汇总 ----------
Write-Host ""
$script:lines | ForEach-Object {
  $color = switch -Wildcard ($_) {
    'PASS*' { 'Green' }
    'FAIL*' { 'Red' }
    'SKIP*' { 'DarkGray' }
    default { 'White' }
  }
  Write-Host $_ -ForegroundColor $color
}
Write-Host ("-" * 66)
Write-Host ("PASS={0}  FAIL={1}  SKIP={2}" -f $script:pass, $script:fail, $script:skip) -ForegroundColor $(if ($script:fail -gt 0) { 'Red' } else { 'Green' })
exit $(if ($script:fail -gt 0) { 1 } else { 0 })
