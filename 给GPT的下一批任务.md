# 下一批任务（给后端 AI）—— 优先级从高到低

> 背景：M1 已在这台机器**实测跑通**，不用再怀疑环境。
> 实测输出：
> `GET /api/health` → `{"ok":true,"version":"0.1.0","mock":false}`
> `GET /api/providers` → 六个 provider 全返回，api_key 脱敏为 `***`
> `PUT /api/providers` → 200，写盘成功
> 前端（file:// 打开）切到 real 模式：徽章「真后端」、保存成功、配置能读回。

---

## P0 · 先修这三个我实测出来的问题

### 1. CORS：`allow_origins=['null']` 在这台机器上不够用

**现象**：`GET` 通，`PUT` 在浏览器里一律 `Failed to fetch`。
**排查**：预检 OPTIONS 返回 200 且头齐全（`ACAO: null` / 允许 PUT / 允许 Content-Type），但 Chromium 仍然拒绝。
**原因**：`file://` 页面的 Origin 是 opaque origin，Chromium 只接受 `Access-Control-Allow-Origin: *`，不接受回显 `null`。
**我临时改成的写法**（`app/main.py` 第 15-19 行）：

```python
app.add_middleware(CORSMiddleware, allow_origins=['*'],
                   allow_methods=['GET', 'PUT', 'POST', 'OPTIONS'],
                   allow_headers=['Content-Type', 'Last-Event-ID'])
```

改成 `*` 之后立刻通了。你可以保留，也可以收紧成：

```python
allow_origin_regex=r'^(null|http://(127\.0\.0\.1|localhost)(:\d+)?)$'
```

但**不要**只写 `allow_origins=['null']`，实测不行。

### 2. 状态目录不可写时，返回 500 且响应不带 CORS 头

**现象**：`%LOCALAPPDATA%\GalgameStudio` 在这台机器上"能建目录、不能写文件"
（`PermissionError: [WinError 5]` / `[Errno 13]`），`PUT /api/providers` 直接 500。
**更麻烦的是**：500 响应没有 `Access-Control-Allow-Origin`，浏览器只报 `Failed to fetch`，根本看不出是权限问题。

**要求**：
- `store.atomic_json` 里 mkdir / 写文件失败时，抛一个**带明确 code**的异常，比如
  `STATE_NOT_WRITABLE`，message 里带上**具体路径和原始错误**（`WinError 5` / `Errno 13`）；
- 全局异常处理器返回的响应**也要经过 CORS 中间件**（确保 `Access-Control-Allow-Origin` 存在），否则前端永远只看到 `Failed to fetch`；
- 已经在支持 `GAL_STATE_DIR` 了，这个很好，保持。

**我现在用 `scripts/run-backend.ps1` 把 `GAL_STATE_DIR` 指到项目内 `.state`，问题绕过。**

### 3. `PUT /api/providers` 的校验是"整批失败"

现在 6 个 provider 一起提交，任何一个字段不合法（比如某个 `base_url` 为空）整批 422，
前端拿不到是**哪一个** provider 出的问题。

**要求**：错误响应里加 `field` 字段，例如
```json
{"error": {"code":"INVALID_CONFIG","message":"base_url 必须是不含凭据、查询串的服务地址","where":"providers.save","field":"local_llm.base_url"}}
```

---

## P1 · M2：mock 全链路（这是前端现在唯一在等的东西）

前端已经按契约写好并测过，**字段名一个都别改**，照 §3 实现即可。

### 必须新增的接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects` | `{"name":"雨停之前"}` → `{"id":"p_xxx","path":"..."}` |
| POST | `/api/projects/{id}/generate` | 收 `{requirement, params}` → `{"job_id":"...","sse":"/api/jobs/{job_id}/events"}` |
| GET | `/api/jobs/{job_id}/events` | **SSE**，五类事件 |
| GET | `/api/projects/{id}/graph` | 返回完整 graph.json |
| POST | `/api/projects/{id}/export` | `{"format":"renpy"\|"web"\|"video"}` → `{"job_id":"...","out":"D:\\..."}` |

### SSE 事件（前端已按这个解析，名字必须一字不差）

```
event: agent
data: {"agent":"writer","state":"run","progress":0.35,"msg":"生成第 3/8 个节点台词"}

event: node
data: {"node_id":"n3","patch":{"status":"done","bg":{"asset":"assets/bg/a1f3.png"},"lines":[...]}}

event: log
data: {"level":"info","text":"背景去重：3 个节点复用 1 张"}

event: done
data: {"stats":{"nodes":9,"edges":8,"images":12,"voices":9,"cost":0.0,"elapsed_s":184}}

event: error
data: {"code":"TTS_FAILED","message":"百炼返回 429，已回落本地","where":"voice.batch"}
```

- `agent` 固定四个 key：`writer` / `art` / `voice` / `build`
- `state` 固定：`idle|run|done|failed`
- 每 5 秒发一次 `: keepalive` 注释
- **不要在 done 时一次性发全部节点**，前端靠 `node` 事件增量刷新

### `GAL_MOCK=1` 时

不调任何模型：图表结构正确、资产用占位（纯色 PNG / 1 秒静音 wav），但 **SSE 事件流和 graph.json 必须与真实模式完全一致**，前端才能先接上。

### graph.json 的硬约束（前端渲染依赖这些）

```jsonc
{
  "nodes": [{
    "id": "n3", "type": "choice",          // start | scene | choice | ending
    "title": "两张车票", "status": "done",
    "bg": {"asset": "assets/bg/a1f3.png"},
    "chars": [{"id": "heroine", "pose": "assets/char/h.png", "pos": "center"}],
    "lines": [{"who": "heroine", "text": "票我买好了。", "voice": "assets/voice/n3_01.wav", "dur": 2.31}],
    "choices": [{"label": "「走。」", "to": "n4"}, {"label": "「不走。」", "to": "n5"}],
    "pos": {"x": 680, "y": 320},
    "end_kind": null                        // ending 节点必填 true|bad|alt
  }],
  "edges": [{"id":"e1","from":"n3","port":0,"to":"n4"}]
}
```

- `type` 只有四种；`choice` 必须有 `choices`，数组长度 = 该节点出口数
- `ending` 必须有 `end_kind`，且**没有** `choices`
- `lines` 是数组，一条一句，每条带 `voice` + `dur`（秒，float）
- `asset` 一律是相对项目根的 POSIX 路径

---

## P2 · 之后（额度够再做）

1. **导出 Ren'Py**（M5）：`game/script.rpy` + `images/` + `audio/`，导出的工程要能直接被 Ren'Py SDK 打开跑
2. **单节点重生成**：`POST /api/projects/{id}/nodes/{node_id}/regenerate`，body `{"parts":["bg","char","voice"]}`
3. **断点续跑**：jobs 表落盘，重启后已完成的跳过
4. **图校验**：死链 / 环 / 分支不收敛到结局 / 缺图缺音，不过就带错误重跑（最多 2 次）

---

## 交付方式

每个里程碑给：
```
app/            # 代码
docs/api.md     # 实际实现的接口（与契约有出入要标出来）
docs/changelog.md
scripts/demo.ps1
```

**自测要求**：自己起服务、自己 curl、贴真实输出。**不要写"应该能跑"**。
如果环境跑不起来，就明说卡在哪一步、报什么错——像 M1 那样如实写 `_ssl` 的问题，是对的。
