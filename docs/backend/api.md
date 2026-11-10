# M1 实际接口

当前仅实现下表接口，其他任务书接口均未实现（返回统一 JSON 404），不存在生成/导出的占位成功响应。

|方法|路径|返回|
|---|---|---|
|GET|`/api/health`|`{"ok":true,"version":"0.1.0","mock":false}`，mock 取自 `GAL_MOCK`|
|GET|`/api/providers`|六个配置对象；所有密钥字段一律 `***`|
|PUT|`/api/providers`|支持任务书的配置对象及局部更新，返回脱敏配置|
|POST|`/api/providers/{kind}/test`|`ok`、`latency_ms`、`detail`；失败另带 `error`|

`kind`：`qwen_image`、`comfyui`、`bailian_tts`、`local_tts`、`llm`、`local_llm`。

PUT 密钥 `***` 保持已有值；空字符串清除；其他值写入 DPAPI 加密负载。未识别字段、错误类型、错误 URL、本地并发非 1 均拒绝，整个更新不落盘。

## 错误约定

一般错误返回对应 HTTP 状态（400/404/422/500/502）及：

```json
{"error":{"code":"INVALID_CONFIG","message":"字段类型错误 llm.enabled","where":"providers.save"}}
```

连接探测失败按验收要求返回 HTTP 200、`ok:false`，同时提供错误对象。保留真实异常类型和原文，但替换配置中的密钥。请求校验不回显请求体。

## 探测的真实范围

|供应商|真实调用|成功代表什么|
|---|---|---|
|ComfyUI|GET `/system_stats`，检查 system/devices|ComfyUI 可达，非生图验收|
|云端/本地 LLM|GET `/models`，检查 data 数组|兼容 API 可达；不证明某模型推理权限|
|千问生图|GET `/compatible-mode/v1/models`，Bearer 鉴权|DashScope 模型列表可访问；不证明生图模型权限|
|百炼 TTS|CosyVoice WebSocket 握手|连接可建；不证明指定模型/音色合成权限|
|本地 TTS|GET `/openapi.json`，检查 `/tts` 或 `/infer`|所需服务路由存在；非实际合成验收|

测试接口不受 enabled 影响，即使禁用 provider 也允许手动测试。无云端密钥直接返回 `MISSING_API_KEY`，不会伪造远端成功。请求不自动跟随重定向，不使用环境代理，避免将 Authorization 转发到意外主机。

## 相对任务书的说明

- 未变更字段、接口路径或事件名。尚未实现 SSE，因此没有事件可消费。
- provider 默认全部 disabled，云端密钥为空。
- 百炼配置补充 `model`、`base_url` 默认值；它们是兼容新增项，便于选择账户支持的模型。
- 本包的 `GAL_MOCK=1` 只反映 health 模式标记，**未提供 M2 模拟生成**。
- 已启用 CORS，允许 file:// 的 null Origin 及默认本机 HTTP 源；已对照 ui/api.js，M1 无需变更协议。

## 参考依据

- [阿里云 CosyVoice WebSocket API](https://help.aliyun.com/en/model-studio/cosyvoice-websocket-api)
- [阿里云万相文生图 API](https://help.aliyun.com/en/model-studio/text-to-image-api-reference)

文档中的连接探测不提交收费生图或 TTS 任务。实际模型可用性仍须在后续 M3 验证。

