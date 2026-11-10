# -*- coding: utf-8 -*-
"""
临时桩服务：只用来验证前端 real 模式的链路能不能通。
不是交付给后端的实现，放在 tools/ 下，用完即弃。

实现契约里前端会调的最小集合：
  GET  /api/health
  GET  /api/providers
  POST /api/projects
  POST /api/projects/{id}/generate   -> { job_id, sse }
  GET  /api/jobs/{job_id}/events     -> SSE
  GET  /api/projects/{id}/graph
  POST /api/projects/{id}/export
"""
import asyncio
import json
import time
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

app = FastAPI(title="galgame-stub")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS = {}
PROJECTS = {}


def graph_sample():
    def node(i, t, title, lines, x, y, **kw):
        d = {
            "id": f"n{i}", "type": t, "title": title, "status": "done",
            "bg": {"asset": f"assets/bg/bg{i}.png", "prompt": "雨夜天台", "seed": 1000 + i},
            "chars": [{"id": "heroine", "pose": "assets/char/h.png", "pos": "center"}],
            "lines": [{"who": "她", "text": s, "voice": f"assets/voice/n{i}.wav", "dur": 3.5}
                      for s in lines],
            "pos": {"x": x, "y": y},
            "gen": {"model": "stub", "at": "", "cost": 0},
        }
        d.update(kw)
        return d

    nodes = [
        node(1, "start", "序章", ["雨下得很大。"], 40, 320),
        node(2, "scene", "天台", ["你迟到了。"], 360, 320),
        node(3, "choice", "两张票", ["走吗？"], 680, 320,
             choices=[{"label": "走", "to": "n4"},
                      {"label": "不走", "to": "n5"}]),
        node(4, "ending", "真结局", ["我们走了。"], 1010, 200, end_kind="true"),
        node(5, "ending", "坏结局", ["雨一直在下。"], 1010, 440, end_kind="bad"),
    ]
    edges = [
        {"id": "e1", "from": "n1", "port": 0, "to": "n2"},
        {"id": "e2", "from": "n2", "port": 0, "to": "n3"},
        {"id": "e3", "from": "n3", "port": 0, "to": "n4"},
        {"id": "e4", "from": "n3", "port": 1, "to": "n5"},
    ]
    return {"version": 1, "project": {"id": "p1", "name": "stub"}, "nodes": nodes, "edges": edges}


@app.get("/api/health")
def health():
    return {"ok": True, "version": "0.0.0-stub", "mock": False}


@app.get("/api/providers")
def providers():
    return {
        "qwen_image": {"enabled": True, "api_key": "***", "model": "wanx2.1-t2i-plus",
                       "base_url": "https://dashscope.aliyuncs.com"},
        "bailian_tts": {"enabled": True, "api_key": "***", "model": "cosyvoice-v1",
                        "voice": "longxiaochun", "speed": 0.92,
                        "base_url": "wss://dashscope.aliyuncs.com/api-ws/v1/inference"},
        "llm": {"enabled": True, "api_key": "***", "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat", "temperature": 0.85},
        "comfyui": {"enabled": True, "base_url": "http://127.0.0.1:8188",
                    "workflow_bg": "workflows/galgame_bg.json",
                    "workflow_char": "workflows/galgame_char.json", "concurrency": 1},
        "local_tts": {"enabled": False, "base_url": "http://127.0.0.1:9880", "engine": "gpt-sovits"},
        "local_llm": {"enabled": False, "base_url": "http://127.0.0.1:11434/v1",
                      "model": "qwen2.5:14b"},
    }


@app.put("/api/providers")
def save_providers(payload: dict):
    return providers()


@app.post("/api/providers/{kind}/test")
def test_provider(kind: str):
    if kind in ("comfyui", "local_tts"):
        return {"ok": False, "detail": "stub：本机没起 " + kind}
    return {"ok": True, "latency_ms": 123}


@app.post("/api/projects")
async def create_project(payload: dict):
    pid = "p_" + uuid.uuid4().hex[:8]
    PROJECTS[pid] = str(payload.get("name") or "未命名")
    return {"id": pid, "path": f"projects/{pid}"}


@app.get("/api/projects/{pid}/graph")
def get_graph(pid: str):
    return graph_sample()


@app.post("/api/projects/{pid}/generate")
async def generate(pid: str, payload: dict):
    job = uuid.uuid4().hex[:8]
    JOBS[job] = time.time()
    return {"job_id": job, "sse": f"/api/jobs/{job}/events"}


@app.get("/api/jobs/{job}/events")
async def events(job: str):
    async def gen():
        def ev(name, data):
            return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        steps = [
            ("agent", {"agent": "writer", "state": "run", "progress": 0.4, "msg": "写台词"}),
            ("log", {"level": "info", "text": "stub：台词 5 段"}),
            ("agent", {"agent": "writer", "state": "done", "progress": 1, "msg": "5 段台词"}),
            ("agent", {"agent": "art", "state": "run", "progress": 0.5, "msg": "出图"}),
            ("log", {"level": "warn", "text": "stub：本地 TTS 未启动，回落云端"}),
            ("agent", {"agent": "art", "state": "done", "progress": 1, "msg": "3 张"}),
            ("agent", {"agent": "voice", "state": "run", "progress": 0.6, "msg": "合成"}),
            ("agent", {"agent": "voice", "state": "done", "progress": 1, "msg": "5 条"}),
            ("agent", {"agent": "build", "state": "run", "progress": 0.8, "msg": "回写"}),
            ("agent", {"agent": "build", "state": "done", "progress": 1, "msg": "校验通过"}),
        ]
        for name, data in steps:
            yield ev(name, data)
            await asyncio.sleep(0.25)
        yield ev("done", {"stats": {"nodes": 5, "edges": 4, "images": 3,
                                    "voices": 5, "cost": 0, "elapsed_s": 3}})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/projects/{pid}/export")
def export(pid: str, payload: dict):
    fmt = payload.get("format", "renpy")
    return {"ok": True, "out": f"D:\\GalgameStudio\\out\\stub\\export\\{fmt}"}
