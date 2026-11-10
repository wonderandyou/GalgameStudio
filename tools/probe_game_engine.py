"""探查 Steam 游戏目录，判断引擎类型。

为什么用 Python：PowerShell 在这个环境里处理长中文脚本会触发
AMSI 的 AccessViolation（进程直接崩），改用 Python 更稳。
"""
import os
import glob

LIBS = [
    r"D:\SteamLibrary\steamapps\common",
    r"C:\Program Files (x86)\Steam\steamapps\common",
]

ENGINE_MARKS = {
    "UnityPlayer.dll": "Unity",
    "UnityCrashHandler64.exe": "Unity",
    "Engine": "Unreal",
    "renpy": "Ren'Py",
    "game": "Ren'Py 的 game 目录",
    "lib": "Ren'Py lib",
    "nw.exe": "NW.js",
    "www": "RPG Maker MV/MZ",
    "data.xp3": "KiriKiri",
    "script.rpyc": "Ren'Py",
    "assets": "Godot/自研",
}

hits = []
for lib in LIBS:
    if not os.path.isdir(lib):
        continue
    for name in os.listdir(lib):
        low = name.lower()
        if "hungry" in low or "lamb" in low or "\u997f\u6b7b" in name:
            hits.append(os.path.join(lib, name))

if not hits:
    print("按名字没找到，列出所有已装游戏：")
    for lib in LIBS:
        if os.path.isdir(lib):
            print("  [" + lib + "]")
            for name in sorted(os.listdir(lib)):
                print("    " + name)
    raise SystemExit(0)

for root in hits:
    print("=" * 60)
    print("游戏目录: " + root)
    print("=" * 60)
    try:
        entries = sorted(os.listdir(root))
    except Exception as e:
        print("  读不了: %r" % (e,))
        continue
    print("--- 顶层内容 ---")
    for e in entries:
        p = os.path.join(root, e)
        if os.path.isdir(p):
            try:
                n = sum(len(files) for _, _, files in os.walk(p))
            except Exception:
                n = -1
            print("  [DIR] %-40s %d 个文件" % (e, n))
        else:
            try:
                mb = os.path.getsize(p) / 1024 / 1024
            except Exception:
                mb = -1
            print("  %-46s %8.2f MB" % (e, mb))

    print("--- 引擎标记 ---")
    for mark, engine in ENGINE_MARKS.items():
        if os.path.exists(os.path.join(root, mark)):
            print("  ✓ %-28s → %s" % (mark, engine))

    for sub in ("game", "Game", "lib", "renpy", "www"):
        p = os.path.join(root, sub)
        if not os.path.isdir(p):
            continue
        print("--- %s/ 里的关键文件 ---" % sub)
        try:
            names = os.listdir(p)
        except Exception:
            continue
        rpy = [n for n in names if n.endswith((".rpy", ".rpyc"))]
        print("  .rpy/.rpyc 文件: %d 个" % len(rpy))
        for n in rpy[:15]:
            print("    " + n)
        if len(rpy) > 15:
            print("    ...（共 %d 个）" % len(rpy))
        for extra in ("gui.rpy", "gui.rpyc", "screens.rpy", "screens.rpyc", "options.rpy", "options.rpyc",
                      "script.rpy", "script.rpyc", "gui", "images", "audio"):
            q = os.path.join(p, extra)
            if os.path.exists(q):
                kind = "DIR" if os.path.isdir(q) else "FILE"
                print("  ✓ %s %s" % (kind, extra))
