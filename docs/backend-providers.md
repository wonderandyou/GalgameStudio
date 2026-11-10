# Provider 层：真实调用与降级（后端）

> 2026-11-06 实现。文件：`app/providers/{base,llm,image,tts,registry}.py` + `app/engine/pipeline.py`。

## 1. 总览

`GAL_MOCK=1`（默认，测试用）：完全不碰网络，走 `app/mock_api.py` 的纯 mock 快速路径。

`GAL_MOCK=0`：走真实流水线 `app/engine/pipeline.py`；**每个阶段**先看 provider 能不能真调，
不能就降级成 mock 素材，并在**三处**都标出来：

| 位置 | 字段 |
|---|---|
| `POST /api/projects/{id}/generate` 响应 | `mock: true/false` + `degraded: {stage: 原因}` |
| SSE 事件 | 降级时发 `log(level=warn)`，说明哪个阶段、为什么 |
| `done` 事件 | `data.mock` 与 `data.stats.{mock, stages, degraded}` |

**没有任何一条路径会因为「没 key」返回 500。** 也不会静默成功。

## 2. 三个阶段与 provider

| 阶段 | 默认 provider | 接口 | 实现状态 |
|---|---|---|---|
| 编剧 writer | `llm`（DeepSeek / OpenAI 兼容），备选 `local_llm` | `POST {base}/chat/completions` | ✅ 已实现（HTTP，OpenAI 兼容） |
| 美术 art | `qwen_image`（DashScope 千问） | `POST {base}/compatible-mode/v1/images/generations` | ⚠ 已实现但**未用真 key 验证**（形状按 DashScope OpenAI 兼容模式写） |
| 配音 voice | `bailian_tts`（百炼） | `POST {base}/api/v1/services/aigc/multimodal-generation/generation`（Qwen-TTS） | ⚠ 已实现但**未用真 key 验证**；默认配置是 CosyVoice `wss://`，**这条路不可用**（见 §5） |

每个 provider 都是 `BaseProvider` 子类，实现：

```python
is_available() -> bool          # 启用 + 有 key + 传输方式支持
unavailable_reason() -> str     # 不可用的具体原因（进降级日志）
async call(...)                 # 真调；失败一律抛 ProviderError
```

## 3. 降级规则（逐阶段）

- provider 为 `None`（没配/没启用/没 key） → 直接降级，不发起任何网络请求。
- `call()` 抛任何异常（`ProviderError` / 超时 / 解析失败 / 结构不符） → 捕获，降级。
- 编剧：模型返回的 JSON 不符合节点骨架（缺节点、多节点、空台词）→ 降级回内置台词。
- 配音：模型返回的不是可解析 WAV（拿不到真实时长）→ 降级，因为无法和 `dur` 对齐。
- 装配：`validate_graph` 永远要过；真实阶段写坏的数据不会流到 graph.json。

**拓扑不受模型影响**：节点数/分支永远由 `params`（`node_count` / `branch_depth`）决定，
模型只负责标题和台词 —— 这样真实模型输出格式崩了也不会毁掉整张图。

## 4. 配音时长对齐（关键约束）

`validate_graph` 要求：语音 WAV 实测时长与 `line.dur` 差 < 0.15s，且 `dur` 与字数
（`len(text)/4.5`）偏差 < 60%。

- 真实 TTS：`dur = WAV 实测时长`，天然一致。
- 降级：按 `len(text)/4.5` 生成**精确时长**的静音 WAV（`pipeline.silent_wav_for`），
  不是固定 1 秒 —— 固定 1 秒在长台词时会触发「偏差 > 60%」校验失败。

## 5. 已知限制（如实说明，未粉饰）

1. **TTS 默认配置不可用**：`config.DEFAULTS['bailian_tts']` 是 `wss://.../api-ws/v1/inference`
   （CosyVoice 流式协议）。本机没装 websocket 客户端库，且本项目不允许 `pip install`，
   所以 `is_available()` 返回 False 并给出原因。要走真实合成，需把配置改成
   `base_url=https://dashscope.aliyuncs.com`、`model=qwen3-tts-flash`、`voice=Cherry`。
   我没有改默认值（前端界面会显示默认值，改了需和前端同步）。
2. **图像与 TTS 的真实调用没验过**：没有 API key。代码按官方文档写，
   请求形状/响应解析由单元测试用假 `request` 验证过；**端到端真调未验证**。
3. **文本 provider 同上**：同样没有 key，只验证到请求形状与解析。
4. **成本没有估算**：`stats.cost` 固定 0.0（没有可靠价目表，不编数字）。
5. **CosyVoice WebSocket 合成未实现**：需要 ws 客户端（现有 `websocket_probe.py` 只能握手）。

## 6. 测试

`tests/test_providers.py`（26 个用例）+ `tests/test_m2.py` 改写的降级用例，覆盖：

- `is_available()`：未启用 / 无 key / ws 协议 / https 可用
- `registry.plan()`：全空 → 三阶段全降级 + `mock=true`；只配 LLM → 两阶段降级；全配 → `mock=false`
- 请求形状：LLM（URL/model/temperature/Authorization）、图像（size 写法 `512*768`、b64 与 url 两种响应）、
  TTS（端点、voice、base64 WAV 解析、非 WAV 拒绝）
- 流水线：全真 → `stages` 全 real 且 `mock=false`；编剧失败/JSON 不合法/图像失败/语音失败 → 对应阶段 mock 且图仍合法
- `auto_voice=false` → 不调 TTS，`voice=None`
- 集成：`GAL_MOCK=0` + 空配置 → `POST /generate` 200 且 `mock:true`，SSE `done` 带 `mock:true`，图可过 `validate_graph`

跑法（pytest 未安装，用标准库）：

```powershell
$env:GAL_STATE_DIR='D:\软件\GalgameStudio\.state'
& 'app\.venv\Scripts\python.exe' -m unittest discover -s tests -p 'test_*.py' -v
```
