# 本次验证记录

## 真正通过的检查

- 19 个 Python 文件、3 个 PowerShell 脚本语法检查，见 syntax-check.txt。
- 1,098 组 graph 组合（节点数 4–64，分支深度 0–8，语音 true/false）。
- 拒绝有环图与 edges/choices 不一致图。
- PNG 块 CRC 与解压像素长度。
- WAV 为 24kHz/16bit/单声道/1.000 秒。

原始输出见 contract-test-output.txt；这些不是 HTTP/SSE 验收。

## 实际未通过的验收

始终使用用户指定的 D:\软件\GalgameStudio\app\.venv\Scripts\python.exe。

运行 scripts/m2_smoke.ps1（已真正执行）在导入 ssl 的 preflight 阶段失败：

```text
ImportError: DLL load failed while importing _ssl: 动态链接库(DLL)初始化例程失败。
```

见 m2-preflight-output.txt。服务没有启动，所以 **未取得 curl -N 的真实 SSE 输出**。没有用手写示例替代实测记录。

完整 unittest 运行：11 个测试在创建临时目录时失败（Python 没有可用的可写临时目录），见 http-test-output.txt。进一步剥离临时文件依赖运行 3 个 P0 回归：仍因 anyio 导入 ssl 失败而无法请求，见 p0-test-output.txt。堆栈中的 anyio[asyncio] 安装提示是包装错误，根因仍为 _ssl；不建议因此重装包。

另外直接 Python 写入已授权 work 目录也报 PermissionError。已经申请并获得该测试目录写权限和 Python 运行时读取权限，重试仍失败。未确定根因，未修改系统 DLL、ACL 或用户 Python 环境。

## 尚待验收

- 本机启动 uvicorn 后的真实 curl -N 事件流、5 秒心跳。
- CORS 预检与 422/500 的浏览器表现。
- 凭据权限错误提示和 DPAPI 实际保存。
- 任务持久化/重启恢复、取消、资源 HTTP、导出后试玩。

scripts/m2_smoke.ps1 已包括 HTTP/SSE、graph、资源格式相关回归、导出和取消流入口，全部通过才打印 M2_SMOKE_PASSED。断点恢复代码已实现，但本次没有 kill/restart 实测，不能标为验收通过。
