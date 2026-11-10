# 实际 API · P0 + M2

默认地址 http://127.0.0.1:8790。M2 需 GAL_MOCK=1。错误统一为 `{ "error": { "code": "...", "message": "...", "where": "...", "field": "..." } }`；field 仅参数错误时出现。

## P0

CORS 包在 FastAPI 的 ServerErrorMiddleware 外面，允许 `*`，支持 file:// Origin:null，且 500 不丢失 CORS 头。不启用 allow_credentials。

文件创建、父目录创建、写入和替换失败返回 HTTP 500、STATE_NOT_WRITABLE，message 含目标路径、异常类型及原始错误。读文件失败为 STATE_NOT_READABLE。无静默目录回落；使用 GAL_STATE_DIR 显式覆盖凭据目录。

PUT /api/providers 原子校验，错误示例：`error.field="local_llm.base_url"`。错误 URL、类型、未知字段、并发和速度范围均提供 field。请求模型校验也提供字段路径。返回及日志脱敏规则不变。

## 接口

|方法|路径|请求/返回|
|---|---|---|
|GET|/api/health|ok/version/mock|
|GET/PUT|/api/providers|六 provider 配置；密钥 ***|
|POST|/api/providers/{kind}/test|ok/latency_ms/detail；失败另带 error|
|POST|/api/projects|`{"name":"雨停之前"}` → id/path|
|GET|/api/projects|项目元数据列表|
|POST|/api/projects/{id}/generate|requirement/params → job_id/sse|
|GET|/api/projects/{id}/graph|完整 graph.json；生成期间可读当前状态|
|GET|/api/jobs/{job_id}/events|标准 SSE；支持 Last-Event-ID|
|POST|/api/jobs/{job_id}/cancel|真正取消异步任务，写入 JOB_CANCELLED error 终态|
|POST|/api/projects/{id}/export|`{"format":"renpy"}` 或 web → job_id/out|
|GET|/api/assets/{id}/{path}|项目 assets 下 PNG/WAV，拒绝路径越界|

同项目同时只能有一个任务，冲突返回 409 PROJECT_BUSY。未完成图导出返回 409 GRAPH_NOT_READY。video 返回 501 EXPORT_NOT_SUPPORTED。非 mock 生成返回 501 REAL_GENERATION_NOT_IMPLEMENTED。未知项目/任务返回 JSON 404。

## Generate

params 默认：node_count=8、branch_depth=2、auto_voice=true、consistency=true、resolution=[1920,1080]。node_count 范围 4–64，branch_depth 范围 0–8，resolution 每边 64–8192。Mock 固定剧情，只用于协议联调，不响应需求语义。

项目位于 GAL_ROOT/projects/{id}，默认 GAL_ROOT 为本包根目录；任务事件/阶段进度位于 GAL_ROOT/.jobs。凭据目录独立使用 GAL_STATE_DIR 或 LOCALAPPDATA 默认值。

## SSE 契约

- agent：agent=writer/art/voice/build；state=idle/run/done/failed；progress、msg。
- node：node_id、patch。writer 时逐个发送完整初始节点；art/voice/build 分别发送资产、台词、完成状态增量，绝非等到 done 再集中发送。
- log：level、text。
- done：stats 含 nodes/edges/images/voices/cost/elapsed_s。
- error：code、message、where。失败或取消时发送，不在成功路径伪造错误。

每条为 `id:` + `event:` + 单行 JSON `data:` + 空行，每 5 秒 `: keepalive`。成功流有 agent/node/log/done；失败/取消流有 error，验收脚本分别覆盖。断线默认从头回放；Last-Event-ID 从下一条回放。客户端断线不取消任务。

队列串行执行并落盘，重启后 GAL_MOCK=1 自动恢复未完成任务，跳过已完成阶段；部分阶段可能重新发送节点 patch，ID 相同且覆盖式应用。不得将此 M2 阶段恢复宣称为真实模型计费幂等。

## graph 与资源

app/contract.py 定义 Pydantic 模型；app/engine/graph.py 校验唯一 start n1、至少一个 ending、合法分支、终点无 choices、全图可达、无环、冗余 edges 与 choices 一致、相对 POSIX 资产路径、语音时长等。

命令：`指定解释器 -m app.engine.graph --check projects/{id}/graph.json`；关闭语音生成时加 `--no-voice`。

初始空项目以及生成中的 graph 是草稿，最终 done 前必须通过校验。非 ending 节点通过 choices 指向后继；ending 含 end_kind 且不含 choices。资产路径例如 assets/bg/mock.png。

## 导出

提交导出后返回 out 是目标路径，必须等该 job 的 done 才表示完成。Ren'Py 生成 game/script.rpy、options.rpy、assets；复制到 Ren'Py SDK 工程中试玩。网页包含嵌入 graph 的 index.html 和 assets，可直接 file:// 打开。使用纯色和静音，不是最终模型生成素材。SDK 运行和浏览器试玩未实机验收。

## 尚未实现

PUT graph、单节点 regenerate、真实模型生成、录像；不返回伪成功。相较原协议没有字段/路径/事件改名；新增可选 error.field 和 SSE id 行，不需要替换前端契约。
