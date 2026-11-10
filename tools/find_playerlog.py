"""找最近被写过的 Unity Player.log，并打印尾部，用于诊断游戏为什么秒退。"""
import os
import time
import glob

ROOT = os.path.expanduser(r"~\AppData\LocalLow")
candidates = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    for fn in filenames:
        if fn.lower() == "player.log":
            p = os.path.join(dirpath, fn)
            try:
                candidates.append((os.path.getmtime(p), p))
            except Exception:
                pass

if not candidates:
    print("LocalLow 下没有 Player.log")
else:
    candidates.sort(reverse=True)
    print("找到 %d 个 Player.log，最新 6 个：" % len(candidates))
    for mt, p in candidates[:6]:
        print("  %s  %s" % (time.strftime("%m-%d %H:%M:%S", time.localtime(mt)), p))
    print()
    mt, newest = candidates[0]
    print("=== 最新那个的尾部（%s）===" % newest)
    with open(newest, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    print("总行数: %d" % len(lines))
    for line in lines[-60:]:
        print("  " + line.rstrip())
    print()
    print("=== 关键字命中 ===")
    kw = ["Exception", "Error", "error", "Crash", "crash", "Shader", "D3D", "fail", "Fail", "Steam"]
    for k in kw:
        hits = [l.rstrip() for l in lines if k in l]
        if hits:
            print("  [%s] %d 条，最后一条: %s" % (k, len(hits), hits[-1][:200]))
