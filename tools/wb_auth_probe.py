"""探测 codebuddy 的登录接口，看能不能拿到可直接打开的登录链接。

POST /api/v1/auth/login 可能需要 body，先试空 body，再试几种常见结构。
只打印响应，不打印任何凭据值。
"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8899"
HDR = {"x-codebuddy-request": "1", "Content-Type": "application/json"}


def call(path, method="GET", body=None, timeout=30):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=HDR, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)


print("=== openapi: auth/login 结构 ===")
s, t = call("/api/openapi.json")
if s == 200:
    spec = json.loads(t)
    node = spec["paths"].get("/api/v1/auth/login", {}).get("post", {})
    print("summary:", node.get("summary"))
    print("description:", (node.get("description") or "")[:800])
    rb = node.get("requestBody") or {}
    for ct, c in (rb.get("content") or {}).items():
        print("ct:", ct)
        print("schema:", json.dumps(c.get("schema"), ensure_ascii=False)[:1500])
    print("responses:", json.dumps(node.get("responses"), ensure_ascii=False)[:800])

print()
print("=== 试 POST /api/v1/auth/login（空 body）===")
print(call("/api/v1/auth/login", "POST", {})[0], call("/api/v1/auth/login", "POST", {})[1][:800])

print()
print("=== 再查 auth/status ===")
print(call("/api/v1/auth/status")[1][:500])
