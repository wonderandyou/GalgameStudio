# -*- coding: utf-8 -*-
"""
从 PyPI 官方 API 取 wheel 的登记哈希，再从 files.pythonhosted.org 官方 CDN 下载，
逐个做 SHA256 比对。不经过任何镜像，也不依赖 pip。

输出：控制台汇总 + _哈希校验报告.json
"""
import hashlib
import json
import pathlib
import re
import sys
import time
import urllib.request

APP = pathlib.Path(r"D:\软件\GalgameStudio\app")
WHL = APP / "_whl"
REQ = APP / "requirements.txt"
REPORT = APP / "_哈希校验报告.json"
UA = {"User-Agent": "galgame-verify/1.0 (local)"}


def http_json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def http_bytes(url, timeout=120):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_req():
    out = []
    for line in REQ.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def pick_wheel(files):
    """优先本机平台(cp311/win_amd64)，其次纯 python wheel"""
    cands = [f for f in files if f["filename"].endswith(".whl")]
    if not cands:
        return None

    def score(f):
        n = f["filename"]
        s = 0
        if "cp311" in n:
            s += 60
        if "win_amd64" in n:
            s += 40
        if "py3-none-any" in n or "py2.py3-none-any" in n:
            s += 30
        return s

    cands.sort(key=score, reverse=True)
    return cands[0]


def main():
    WHL.mkdir(exist_ok=True)
    pkgs = parse_req()
    print("待校验包 %d 个" % len(pkgs))

    rows = []
    ok = bad = skip = 0

    for name, ver in pkgs:
        t0 = time.time()
        try:
            data = http_json("https://pypi.org/pypi/%s/%s/json" % (name, ver))
        except Exception as e:
            print("[SKIP] %-22s %-10s 官方 API 取不到：%s" % (name, ver, e))
            rows.append({"name": name, "version": ver, "result": "skip", "reason": str(e)})
            skip += 1
            continue

        f = pick_wheel(data.get("urls", []))
        if not f:
            print("[SKIP] %-22s %-10s 无 wheel" % (name, ver))
            rows.append({"name": name, "version": ver, "result": "skip", "reason": "no wheel"})
            skip += 1
            continue

        fn = f["filename"]
        official = f["digests"]["sha256"]
        path = WHL / fn

        try:
            if not path.exists() or path.stat().st_size != f["size"]:
                blob = http_bytes(f["url"])
                path.write_bytes(blob)
            blob = path.read_bytes()
        except Exception as e:
            print("[SKIP] %-22s %-10s 下载失败：%s" % (name, ver, e))
            rows.append({"name": name, "version": ver, "result": "skip", "reason": str(e)})
            skip += 1
            continue

        actual = hashlib.sha256(blob).hexdigest()
        size_ok = len(blob) == f["size"]
        same = (actual == official)

        if same and size_ok:
            ok += 1
            tag = "OK  "
        elif same and not size_ok:
            bad += 1
            tag = "SIZE"
        else:
            bad += 1
            tag = "MISM"

        print("[%s] %-22s %-10s %7.1fKB  %.1fs" % (tag, name, ver, len(blob) / 1024, time.time() - t0))
        rows.append({
            "name": name, "version": ver, "filename": fn,
            "bytes": len(blob), "size_matches_api": size_ok,
            "sha256_actual": actual, "sha256_official": official,
            "result": "ok" if (same and size_ok) else "mismatch",
            "url": f["url"],
        })

    summary = {"total": len(pkgs), "ok": ok, "mismatch": bad, "skip": skip,
               "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "items": rows}
    REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 56)
    print("结果：OK=%d  MISMATCH=%d  SKIP=%d  共 %d" % (ok, bad, skip, len(pkgs)))
    print("报告：%s" % REPORT)
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
