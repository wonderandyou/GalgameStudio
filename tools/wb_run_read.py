"""读 WorkBuddy codebuddy API 的 run 结果流。

用途：主人让我"注入 WorkBuddy 替我干活"——
本机 WorkBuddy 自带 codebuddy CLI（v2.165.0），可以用
    WorkBuddy.exe <cli>/dist/codebuddy-lite-wb.mjs --serve --port 8899 --auth none
起一个本地 HTTP 网关，然后 POST /api/v1/runs 发提示词。
本脚本负责：发提示词 -> 读 SSE 流 -> 把 Agent 输出拼出来。

注意：所有 /api/v1/* 请求都必须带 x-codebuddy-request 头，否则 403。
"""
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8899"
HDR = {"x-codebuddy-request": "1", "Content-Type": "application/json"}


def call(path, method="GET", body=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=HDR, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)


def start_run(text, sender_id="jingshan", sender_name="鲸鲸"):
    payload = {
        "id": str(uuid.uuid4()),
        "type": "text",
        "text": text,
        "sender": {"id": sender_id, "name": sender_name},
    }
    status, body = call("/api/v1/runs", "POST", payload, timeout=90)
    if status not in (200, 201, 202):
        raise RuntimeError("启动失败 %s: %s" % (status, body[:400]))
    return json.loads(body)["data"]["runId"]


def read_stream(run_id, seconds=90):
    texts = []
    deadline = time.time() + seconds
    req = urllib.request.Request(BASE + "/api/v1/runs/" + run_id + "/stream", headers=HDR)
    try:
        with urllib.request.urlopen(req, timeout=seconds + 10) as r:
            for raw in r:
                if time.time() > deadline:
                    print("\n(读流超时截断)", flush=True)
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                payload = line[5:].strip() if line.startswith("data:") else line
                if payload in ("[DONE]", "done"):
                    break
                try:
                    obj = json.loads(payload)
                except Exception:
                    print("RAW:", payload[:300], flush=True)
                    continue
                kind = obj.get("sessionUpdate")
                content = obj.get("content") or {}
                if kind == "agent_message_chunk":
                    piece = content.get("text", "")
                    texts.append(piece)
                    print(piece, end="", flush=True)
                elif kind == "user_message_chunk":
                    print("[我] " + content.get("text", "")[:150], flush=True)
                else:
                    brief = json.dumps(obj, ensure_ascii=False)
                    print("\n[%s] %s" % (kind, brief[:300]), flush=True)
    except urllib.error.HTTPError as e:
        print("流读取失败 %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:400]), flush=True)
    except Exception as e:
        print("流异常: %r" % (e,), flush=True)
    return "".join(texts)


if __name__ == "__main__":
    prompt = sys.argv[1] if len(sys.argv) > 1 else "只回答一行：当前工作目录里有没有 GalgameStudio 这个文件夹？"
    wait = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    print("=== health ===", flush=True)
    print(call("/api/v1/health")[1][:200], flush=True)
    print("\n=== 发提示词 ===", flush=True)
    print(prompt, flush=True)
    rid = start_run(prompt)
    print("runId: " + rid, flush=True)
    print("\n=== Agent 输出 ===", flush=True)
    answer = read_stream(rid, wait)
    print("\n\n=== 完整答复 ===", flush=True)
    print(answer, flush=True)
