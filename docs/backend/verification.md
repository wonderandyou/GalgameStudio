# M1-r2 实测记录

解释器：D:\软件\GalgameStudio\app\.venv\Scripts\python.exe。

- importlib.find_spec 确认 FastAPI、uvicorn、httpx 已安装。
- 直接 PowerShell 调用 .NET ProtectedData 的加密、解密和明文比对成功：PASS native DPAPI API。
- Python 与 PowerShell 语法检查见 syntax-check.txt。

实际运行 `指定解释器 -I -S -c "import ssl"`，返回：

```text
ImportError: DLL load failed while importing _ssl: 动态链接库(DLL)初始化例程失败。
```

非隔离模式同样失败。_ssl.pyd、libssl-3.dll、libcrypto-3.dll 都存在，_ssl.pyd 签名 Valid。ctypes 导入也报 DLL 初始化错误；Python 启动 powershell.exe 和 pwsh.exe 返回 3221225794（0xC0000142），启动 cmd.exe 返回 0。

未确定这些错误的根因，未修改 DLL 或现有环境。Python 写入交付目录也遇到 PermissionError，可能与当前执行环境的访问权限有关，但尚未证实。

因此 uvicorn 未启动，health、ComfyUI 接口没有后端真实输出，unittest 未通过运行验收。没有伪造 curl 输出。

用户已安排自行测试。运行 scripts/demo.ps1 可取得真实 HTTP 输出与 unittest 结果。检查失败时脚本会保留错误并停止，不伪报成功。scripts/start.ps1 用于正常启动；均不安装依赖。

本包仅 M1，M2–M6 未完成。
