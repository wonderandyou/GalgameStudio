# 变更记录

## 0.1.0-M1-source · 2026-11-06

- 新建 FastAPI M1 骨架、六 provider 配置、真实连接探测。
- 加入 DPAPI 加密存储、并发写入锁、原子落盘和脱敏处理。
- 加入统一错误响应、配置约束、真实错误地址测试用例。
- 提供启动和验收 PowerShell 脚本、API 说明与交付边界。
- 原项目和 ui 目录未写入。
- **状态：源码待运行验收；不是完整后端发布版。**

## M1-r2 环境适配

- 使用用户现有解释器；启动脚本不再安装依赖。
- 允许 file:// null Origin CORS，未修改前端。
- DPAPI 与 WebSocket 握手改用 Windows 自带 .NET，移除 pywin32/websockets 依赖。
- 使用标准库 unittest，6 个测试方法，无需 pytest。
- 记录实际 SSL/子进程初始化阻塞；未标记 M1 运行验收通过。
