"""使用 Windows 自带的 .NET DPAPI；不需要额外安装 pywin32。"""
import base64
import os
import subprocess

SCRIPT = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$data = [Convert]::FromBase64String($request.data)
$scope = [System.Security.Cryptography.DataProtectionScope]::CurrentUser
if ($request.operation -eq 'protect') {
    $result = [System.Security.Cryptography.ProtectedData]::Protect($data, $null, $scope)
} else {
    $result = [System.Security.Cryptography.ProtectedData]::Unprotect($data, $null, $scope)
}
[Console]::Out.Write([Convert]::ToBase64String($result))
'''

def transform(data: bytes, operation: str) -> bytes:
    import json
    if os.name != 'nt':
        raise RuntimeError('凭据加密仅支持 Windows DPAPI')
    # 凭据只经过 stdin/stdout 管道，不放命令行、临时文件或异常消息。
    result = subprocess.run(
        ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', SCRIPT],
        input=json.dumps(dict(operation=operation, data=base64.b64encode(data).decode('ascii'))),
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError('Windows DPAPI 操作失败')
    return base64.b64decode(result.stdout.strip(), validate=True)
