# 后端基线（Backend Baseline）

> 记录时间：2026-11-06 23:10 ~ 23:20
> 记录人：backend-dev（后端）
> 本文档只写实测结果，不写推测。

---

## 0. 环境坑（先看这里，别再踩）

| 项 | 事实 |
|---|---|
| **venv 位置** | `D:\软件\GalgameStudio\app\.venv\Scripts\python.exe`（**不是**项目根的 `.venv`，根目录没有 `.venv`） |
| Python 版本 | 3.11.9 |
| **pytest** | **没装**（`No module named 'pytest'`）。`app\requirements.txt` 里的 19 个依赖不含 pytest。 |
| 测试跑法 | 用标准库：`python -m unittest discover -s tests -p 'test_*.py' -v` |
| **git** | **本项目不是 git 仓库**（`fatal: not a git repository`）→ 证明「没改 ui/」改用 SHA256 快照（见 §4） |
| `GAL_STATE_DIR` | 必须指到可写目录：`D:\软件\GalgameStudio\.state`（默认 `%LOCALAPPDATA%` 在本机 WinError 5） |
| 递归找文件 | 必须排除 `.venv`，否则输出被 pip/pydantic 几千个文件淹没 |
| 启动服务 | `GAL_MOCK=1 GAL_STATE_DIR=...\.state python -m uvicorn app.main:app --host 127.0.0.1 --port 8790` |

---

## 1. 基线测试（改动前）

命令：

```powershell
cd D:\软件\GalgameStudio
$env:GAL_STATE_DIR='D:\软件\GalgameStudio\.state'
& 'app\.venv\Scripts\python.exe' -m unittest discover -s tests -p 'test_*.py' -v
```

原始输出尾部：

```
test_encrypted_roundtrip (test_m1.M1Tests.test_encrypted_roundtrip) ... ok
test_health_and_cors (test_m1.M1Tests.test_health_and_cors) ... ok
test_missing_key (test_m1.M1Tests.test_missing_key) ... ok
test_redaction (test_m1.M1Tests.test_redaction) ... ok
test_refused_connection (test_m1.M1Tests.test_refused_connection) ... ok
test_validation (test_m1.M1Tests.test_validation) ... ok
test_generation_and_exports (test_m2.M2Tests.test_generation_and_exports) ... ok
test_heartbeat_and_live_nodes (test_m2.M2Tests.test_heartbeat_and_live_nodes) ... ok
test_mock_off_does_not_generate (test_m2.M2Tests.test_mock_off_does_not_generate) ... ok
test_p0_error_headers_and_fields (test_m2.M2Tests.test_p0_error_headers_and_fields) ... ok
test_voice_disabled_and_error_stream (test_m2.M2Tests.test_voice_disabled_and_error_stream) ... ok
test_global_500_cors (test_p0.P0Tests.test_global_500_cors) ... ok
test_preflight_and_validation (test_p0.P0Tests.test_preflight_and_validation) ... ok
test_write_error_path_and_cors (test_p0.P0Tests.test_write_error_path_and_cors) ... ok

----------------------------------------------------------------------
Ran 14 tests in 19.002s

OK
```

**14 passed / 0 failed。**

---

## 2. 服务启动 + `/openapi.json` 端点

`GET /api/health` 原始响应：

```json
{"ok":true,"version":"0.1.0","mock":true}
```

`/openapi.json` 里的端点（14 个，按路径排序）：

| # | 方法 | 路径 |
|---|---|---|
| 1 | GET | `/api/assets/{project_id}/{asset_path}` |
| 2 | GET | `/api/health` |
| 3 | GET | `/api/jobs/{job_id}/events` |
| 4 | GET | `/api/projects` |
| 5 | GET | `/api/projects/{project_id}/graph` |
| 6 | GET | `/api/providers` |
| 7 | POST | `/api/jobs/{job_id}/cancel` |
| 8 | POST | `/api/projects` |
| 9 | POST | `/api/projects/{project_id}/export` |
| 10 | POST | `/api/projects/{project_id}/generate` |
| 11 | POST | `/api/projects/{project_id}/nodes/{node_id}/regenerate` |
| 12 | POST | `/api/providers/{kind}/test` |
| 13 | PUT | `/api/projects/{project_id}/graph` |
| 14 | PUT | `/api/providers` |

---

## 3. 前端契约对照（`ui/api.js` 是唯一真源，只读不改）

`ui/api.js` 里实际发出的请求（另有 `ui/app.js` / `graph-editor.js` 全部经由 `GalAPI`，没有绕过 api.js 直接 fetch）：

| # | `ui/api.js` 方法 | 请求 | 后端是否存在 | 返回字段期望（前端消费点） |
|---|---|---|---|---|
| 1 | `detect()` | `GET /api/health` | ✅ | `ok`（判 `!!j.ok`） |
| 2 | `getProviders()` | `GET /api/providers` | ✅ | 完整 provider 字典，api_key 为 `***` |
| 3 | `saveProviders(cfg)` | `PUT /api/providers` | ✅ | **判 `r.ok`**（见 §3.1 不一致 #1） |
| 4 | `testProvider(kind)` | `POST /api/providers/{kind}/test` | ✅ | `ok` / `latency_ms` / `detail` / `error` |
| 5 | `ensureProject(name)` | `POST /api/projects` body `{name}` | ✅ | `id` |
| 6 | `getGraph(pid)` | `GET /api/projects/{pid}/graph` | ✅ | `{version, project, characters, nodes, edges}` |
| 7 | `saveGraph(pid, graph)` | `PUT /api/projects/{pid}/graph` | ✅ | `{ok:true}` |
| 8 | `exportProject(pid, format)` | `POST /api/projects/{pid}/export` body `{format}` | ✅ | **判 `r.ok` + 用 `r.out`**（见 §3.1 不一致 #2） |
| 9 | `runGeneration(req, cb)` | `POST /api/projects/{pid}/generate` body `{requirement, params}` | ✅ | `job_id` |
| 10 | `_realRun` | `GET /api/jobs/{jobId}/events`（SSE） | ✅ | 事件名 `agent` / `node` / `log` / `done` / `error` |
| 11 | `cancel(jobId)` | `POST /api/jobs/{jobId}/cancel` | ✅ | —— |
| 12 | `assetURL(pid, rel)` | `GET /api/assets/{pid}/{rel}` | ✅ | PNG / WAV 二进制 |

后端多出的两个（前端暂未用，保留）：`GET /api/projects`（项目列表）、`POST /api/projects/{pid}/nodes/{node_id}/regenerate`（单节点重生成）。

### 3.1 发现的不一致（真 bug：前端会误报失败）

| # | 端点 | 前端期望 | 后端实际 | 后果 |
|---|---|---|---|---|
| 1 | `PUT /api/providers` | `if (r && r.ok)`（`app.js:1675`） | 返回 masked 的全量 provider 字典，**没有 `ok` 字段** | 保存其实成功，界面却提示「✖ 保存接口配置失败：未知」 |
| 2 | `POST /api/projects/{id}/export` | `if (r && r.ok)` + 用 `r.out`（`app.js:1788`） | 返回 `{job_id, out}`，**没有 `ok` 字段** | 文件实际已导出，界面却提示「✖ 导出失败：未知」 |

修法：**只改后端**，给这两个返回体补 `ok: true`（不动 `ui/`）。已存在的测试不断言这两个返回体的具体形状，补字段不会破坏它们。

---

## 4. `ui/` 哈希快照（证明后端没碰前端）

本目录不是 git 仓库，`git status` 不可用，改用 SHA256 快照比对。

**记录时刻：2026-11-06 23:20（此后的对比以这份为基准）**

```
F3E831952AA23DB31FAC6061E26AF709F7762E8B857A4F5DF3E866324FC5851B  12260  ui\api.js  2026-11-06 22:00:37
D6F7F18796C545ECAA9200F3C6F623139D6AFB45C731945177FFF8EF034F5F30  36724  ui\app.css  2026-11-06 22:56:22
0C967673984DA05303EAC38BA2493751CBE7F5AA6701FB261F0DD70BD94C858D  98889  ui\app.js  2026-11-06 23:01:18
CE227A214B2F292B001B7FA3C6DF687B37560E190E1797B83149174F3433664D   9063  ui\e2e.js  2026-11-06 23:13:51
1749D524D359215DDB507B11E635D44309962800668BEEC43D13D338B116FF56  21156  ui\graph-editor.js  2026-11-06 23:02:36
879E1ABC345ECE46C6932E2CCF61086F22AF2C422420F3F9D0ADEE5AD85103AB  30387  ui\index.html  2026-11-06 23:14:04
56FE8D497F6560F596029C7A5F6D26822B585D8163ECB8FDB749931CBAD45312  11780  ui\node-lab.html  2026-11-06 22:17:24
565CEE8D54E7DFD16295F0EC7B19F910A739C0F42D5263198E3416A38A6006B3  12820  ui\vendor\litegraph.css  1985-10-26 16:15:00
A53F4A5BFB85520CE11A652C4E883757F477CC62873C1438E0ADE330106A24E4 1074483  ui\vendor\litegraph.js  1985-10-26 16:15:00
```

⚠ 注意：`ui/e2e.js`（23:13:51）和 `ui/index.html`（23:14:04）是 **Lead 前端那边同时在改的**，如果这两行在最终对比时变了，是 Lead 改的，不是后端。
后端改动的文件只会有：`app/**`、`tests/**`、`docs/backend-*.md`。

### 4.1 收工时对比（2026-11-06 23:23）

| 文件 | 基线哈希 | 收工哈希 | 结论 |
|---|---|---|---|
| `ui/api.js` | F3E83195…5851B | F3E83195…5851B | **未变** ✅ |
| `ui/app.css` | D6F7F187…F5F30 | D6F7F187…F5F30 | **未变** ✅ |
| `ui/graph-editor.js` | 1749D524…6FF56 | 1749D524…6FF56 | **未变** ✅ |
| `ui/node-lab.html` | 56FE8D49…5312 | 56FE8D49…5312 | **未变** ✅ |
| `ui/vendor/litegraph.css` | 565CEE8D…6006B3 | 565CEE8D…6006B3 | **未变** ✅ |
| `ui/vendor/litegraph.js` | A53F4A5B…24E4 | A53F4A5B…24E4 | **未变** ✅ |
| `ui/app.js` | 0C967673…5C858D | 1260921D…9C2C49 | **变了，是 Lead 改的**（mtime 23:21:11） |
| `ui/e2e.js` | CE227A21…33664D | FAD246F2…54B2D9 | **变了，是 Lead 改的**（mtime 23:23:24，文件从 9 KB 长到 39 KB） |
| `ui/index.html` | 879E1ABC…03AB | B1297DA2…983AA904 | **变了，是 Lead 改的**（mtime 23:23:28） |

**后端自己的写入范围**（全部通过文件编辑工具写，路径可查）：
`app/main.py`、`app/mock_api.py`、`app/providers/{base,llm,image,tts,registry}.py`、
`app/engine/pipeline.py`、`tests/test_m2.py`、`tests/test_providers.py`、
`docs/backend-baseline.md`、`docs/backend-providers.md`。**没有一条指向 `ui/`。**

---

## 5. 后续改动记录

（每改一处追加一行）

- 2026-11-06：补 `PUT /api/providers` 与 `POST .../export` 的 `ok: true` 字段，对齐 `ui/api.js` 契约。
- 2026-11-06：新增 provider 层（`llm` / `image` / `tts` / `registry`）与 `engine/pipeline.py`；
  `GAL_MOCK=0` 从「501 未实现」改为「真跑 + 无 key 降级并标 `mock:true`」。
  `POST /generate` 响应新增 `mock` / `degraded`；`done` 事件新增 `mock` / `stages` / `degraded`。
  详见 `docs/backend-providers.md`。
- 2026-11-06：`tests/test_m2.py` 的 `test_mock_off_does_not_generate` 改名并改断言
  （旧语义是 MOCK=0 必须 501，与新需求「降级返回 mock:true」冲突）；新增 `tests/test_providers.py`。
  测试总数 14 → 40，全绿。
