# -*- coding: utf-8 -*-
"""
演示/开发用 mock 后端（不是正式后端，也不替代 app/ 下 GPT 的 M1）

作用：在真后端 M2 还没到位时，让「填需求 → 生成 → 试玩 → 导出」这条链能真的跑完，
      并且**真的把文件写到磁盘**，方便前端联调和验收。

启动：
  D:\\软件\\GalgameStudio\\app\\.venv\\Scripts\\python.exe -m uvicorn mock_server:app --port 8791

前端连它：
  ui/index.html?mode=real&base=8791

约束：字段名严格照 提示词-后端.md §3，所以将来换成真 M2 时前端一行都不用改。
"""
import asyncio
import base64
import json
import time
import uuid
import wave
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

app = FastAPI(title="galgame-mock", version="0.2.0-mock")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OUT_ROOT = Path(r"D:\软件\GalgameStudio\out")
JOBS, PROJECTS = {}, {}
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


# ---------------- 数据 ----------------
def graph_sample(req_text=""):
    """按需求文本生成一张固定的 7 节点图（结构稳定，方便前端验证）"""
    def node(i, t, title, lines, x, y, **kw):
        d = {
            "id": f"n{i}", "type": t, "title": title, "status": "done",
            "bg": {"asset": f"images/bg/bg{i}.png", "prompt": "雨夜天台", "seed": 1000 + i},
            "chars": [{"id": "heroine", "pose": f"images/char/h{i}.png", "pos": "center"}],
            "lines": [
                {"who": w, "text": s, "voice": f"voice/n{i}_{j+1:02d}.wav", "dur": 3.5}
                for j, (w, s) in enumerate(lines)
            ],
            "pos": {"x": x, "y": y},
            "gen": {"model": "mock", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "cost": 0},
        }
        d.update(kw)
        return d

    nodes = [
        node(1, "start", "序章 · 雨", [("", "十一点四十分，天台的门没锁。")], 40, 320),
        node(2, "scene", "天台 · 她先到了", [("她", "你迟到了七分钟。")], 360, 320),
        node(3, "choice", "两张车票", [("她", "票我买好了。你要走吗？")], 680, 320,
             choices=[{"label": "「走。」", "to": "n4"},
                      {"label": "「……回去吧。」", "to": "n5"}]),
        node(4, "scene", "沉默的三十秒", [("她", "她没说话，只把票塞进我手里。")], 1010, 180),
        node(5, "scene", "楼梯间的回声", [("她", "那我把票退了。")], 1010, 460),
        node(6, "ending", "真结局 · 雨停之前", [("", "我们谁都没有回头。")], 1330, 180,
             end_kind="true"),
        node(7, "ending", "坏结局 · 留在原地", [("", "雨一直下到天亮。")], 1330, 460,
             end_kind="bad"),
    ]
    edges = [
        {"id": "e1", "from": "n1", "port": 0, "to": "n2"},
        {"id": "e2", "from": "n2", "port": 0, "to": "n3"},
        {"id": "e3", "from": "n3", "port": 0, "to": "n4"},
        {"id": "e4", "from": "n3", "port": 1, "to": "n5"},
        {"id": "e5", "from": "n4", "port": 0, "to": "n6"},
        {"id": "e6", "from": "n5", "port": 0, "to": "n7"},
    ]
    return {"version": 1, "project": {"id": "p1", "name": "mock"}, "nodes": nodes, "edges": edges}


# ---------------- Ren'Py 生成 ----------------
def build_rpy(g):
    nodes, edges = g["nodes"], g["edges"]
    out = [
        "# 由奇迹工坊 mock 后端生成（演示用）",
        "# 资源为 1px 占位图 / 1 秒静音，接真模型后替换即可",
        "",
    ]
    start = next((n for n in nodes if n["type"] == "start"), nodes[0])
    out += ["label start:", "    jump " + start["id"], ""]

    for n in nodes:
        out.append("label %s:" % n["id"])
        asset = (n.get("bg") or {}).get("asset", "")
        bg = asset.split("/")[-1].rsplit(".", 1)[0] if asset else "rainy"
        out.append("    scene bg " + bg)
        for ln in n.get("lines", []):
            if ln.get("voice"):
                out.append('    voice "%s"' % ln["voice"])
            text = str(ln.get("text", "")).replace('"', '\\"')
            who = ln.get("who") or ""
            out.append('    "%s" "%s"' % (who, text) if who else '    "%s"' % text)
        if n["type"] == "choice" and n.get("choices"):
            out.append("    menu:")
            for c in n["choices"]:
                out.append('        "%s":' % str(c["label"]).replace('"', '\\"'))
                out.append("            jump " + c["to"])
        elif n["type"] == "ending":
            out.append("    # " + ("真结局" if n.get("end_kind") == "true" else "坏结局"))
            out.append("    return")
        else:
            nxt = next((e for e in edges if e["from"] == n["id"]), None)
            out.append("    jump " + nxt["to"] if nxt else "    return")
        out.append("")
    return "\n".join(out)


def write_placeholders(game: Path, g):
    count = 0
    for n in g["nodes"]:
        asset = (n.get("bg") or {}).get("asset")
        if asset:
            p = game / asset
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(PNG_1PX)
            count += 1
        for ln in n.get("lines", []):
            v = ln.get("voice")
            if not v:
                continue
            p = game / v
            p.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(p), "w") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(b"\x00\x00" * 24000)
            count += 1
    return count


def safe_name(name):
    """项目名 → 能当目录名的字符串（Windows 非法字符全换成下划线）"""
    import re
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name)).strip().rstrip('.')
    return s or "未命名项目"


# ---------------- 接口 ----------------
@app.get("/api/health")
def health():
    return {"ok": True, "version": "0.2.0-mock", "mock": True}


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
        return {"ok": False, "detail": "mock：本机没起 " + kind}
    return {"ok": True, "latency_ms": 88}


@app.post("/api/projects")
def create_project(payload: dict):
    pid = "p_" + uuid.uuid4().hex[:8]
    PROJECTS[pid] = safe_name(payload.get("name") or "未命名")
    return {"id": pid, "path": str(OUT_ROOT / PROJECTS[pid])}


@app.get("/api/projects/{pid}/graph")
def get_graph(pid: str):
    g = graph_sample()
    g["project"]["name"] = PROJECTS.get(pid, "mock")
    return g


@app.post("/api/projects/{pid}/generate")
def generate(pid: str, payload: dict):
    job = uuid.uuid4().hex[:8]
    JOBS[job] = {"pid": pid, "requirement": str(payload.get("requirement", ""))[:200]}
    return {"job_id": job, "sse": f"/api/jobs/{job}/events"}


@app.get("/api/jobs/{job}/events")
async def events(job: str):
    async def gen():
        def ev(name, data):
            return "event: %s\ndata: %s\n\n" % (name, json.dumps(data, ensure_ascii=False))

        steps = [
            ("agent", {"agent": "writer", "state": "run", "progress": 0.4, "msg": "写台词"}),
            ("log", {"level": "info", "text": "mock：7 段台词，平均 18 字"}),
            ("agent", {"agent": "writer", "state": "done", "progress": 1, "msg": "7 段"}),
            ("agent", {"agent": "art", "state": "run", "progress": 0.5, "msg": "出背景"}),
            ("log", {"level": "warn", "text": "mock：本地 TTS 未启动，回落云端"}),
            ("agent", {"agent": "art", "state": "done", "progress": 1, "msg": "7 张"}),
            ("agent", {"agent": "voice", "state": "run", "progress": 0.6, "msg": "合成"}),
            ("agent", {"agent": "voice", "state": "done", "progress": 1, "msg": "7 条"}),
            ("agent", {"agent": "build", "state": "run", "progress": 0.8, "msg": "回写"}),
            ("agent", {"agent": "build", "state": "done", "progress": 1, "msg": "校验通过"}),
        ]
        for name, data in steps:
            yield ev(name, data)
            await asyncio.sleep(0.22)
        yield ev("done", {"stats": {"nodes": 7, "edges": 6, "images": 7,
                                    "voices": 7, "cost": 0, "elapsed_s": 3}})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/projects/{pid}/export")
def export(pid: str, payload: dict):
    fmt = payload.get("format", "renpy")
    name = safe_name(PROJECTS.get(pid, "未命名项目"))
    out = OUT_ROOT / name
    g = graph_sample()

    if fmt == "renpy":
        game = out / "game"
        (game / "images" / "bg").mkdir(parents=True, exist_ok=True)
        (game / "voice").mkdir(parents=True, exist_ok=True)
        (game / "script.rpy").write_text(build_rpy(g), encoding="utf-8")
        n = write_placeholders(game, g)
        (out / "README-导出说明.txt").write_text(
            "这是 mock 后端生成的 Ren'Py 工程。\n"
            "图片是 1px 占位图、音频是 1 秒静音，接真模型后会替换成真实素材。\n"
            "用法：用 Ren'Py SDK 打开本目录（含 game/ 子目录）即可。\n",
            encoding="utf-8")
        return {"ok": True, "out": str(out), "files": n + 1}
    if fmt == "web":
        (out / "web").mkdir(parents=True, exist_ok=True)
        (out / "web" / "index.html").write_text(
            "<!doctype html><meta charset=utf-8><title>mock</title><p>网页播放器占位</p>",
            encoding="utf-8")
        return {"ok": True, "out": str(out / "web")}
    return {"ok": True, "out": str(out / "trailer")}
