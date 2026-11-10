"""通过 Windows 自带的 ClientWebSocket 测试握手，不发起收费合成。"""
import asyncio
import json
import os
import subprocess
from .base import ProviderError

SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$client = [System.Net.WebSockets.ClientWebSocket]::new()
$client.Options.SetRequestHeader('Authorization', 'Bearer ' + $request.api_key)
$cancel = [System.Threading.CancellationTokenSource]::new(8000)
try {
    $client.ConnectAsync([Uri]$request.url, $cancel.Token).GetAwaiter().GetResult()
    if ($client.State -ne [System.Net.WebSockets.WebSocketState]::Open) { throw 'WebSocket 未打开' }
    [Console]::Out.Write('OK')
} catch {
    [Console]::Error.Write($_.Exception.GetBaseException().Message)
    exit 1
} finally {
    $client.Abort()
    $client.Dispose()
    $cancel.Dispose()
}
'''

async def handshake(url, api_key):
    if os.name != 'nt':
        raise ProviderError('UNSUPPORTED_PLATFORM', '此版本 WebSocket 探测使用 Windows ClientWebSocket', 'bailian_tts.test')
    process = await asyncio.create_subprocess_exec(
        'powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', SCRIPT,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(dict(url=url, api_key=api_key)).encode('ascii')), 12)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode or stdout.strip() != b'OK':
        # Windows PowerShell stderr 编码跟系统区域有关，由外层再次脱敏。
        import locale
        message = stderr.decode(locale.getpreferredencoding(False), errors='replace')
        raise ProviderError('PROVIDER_UNREACHABLE', message[:1000] or 'WebSocket 握手失败', 'bailian_tts.test')
