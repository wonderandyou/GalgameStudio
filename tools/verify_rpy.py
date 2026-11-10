# -*- coding: utf-8 -*-
"""
Ren'Py 导出工程自检
用法：python tools/verify_rpy.py "D:\\软件\\GalgameStudio\\out\\e2e-验收项目"

检查（都是"运行时才会炸"的问题）：
  1. label 定义唯一，且有 start
  2. 所有 jump 目标都已定义
  3. voice 引用的音频文件存在
  4. scene bg X 对应的图片存在（images/bg/X.png 或 images/X.png）
  5. menu 块的选项缩进比 menu 深
  6. 每行 say 的引号成对
"""
import re
import sys
from pathlib import Path

LABEL = re.compile(r"^label\s+([A-Za-z_]\w*)\s*:")
JUMP = re.compile(r"^\s*jump\s+([A-Za-z_]\w*)")
VOICE = re.compile(r'^\s*voice\s+"([^"]+)"')
SCENE = re.compile(r"^\s*scene\s+bg\s+(\S+)")


def main():
    if len(sys.argv) < 2:
        print("用法: python verify_rpy.py <导出工程目录>")
        return 2

    root = Path(sys.argv[1])
    game = root / "game"
    rpy = game / "script.rpy"
    if not rpy.exists():
        print("[FAIL] 找不到 %s" % rpy)
        return 1

    lines = rpy.read_text(encoding="utf-8").splitlines()
    labels, jumps, voices, scenes, problems = [], [], [], [], []

    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue

        m = LABEL.match(line)
        if m:
            labels.append((m.group(1), i))

        m = JUMP.match(line)
        if m:
            jumps.append((m.group(1), i))

        m = VOICE.match(line)
        if m:
            voices.append((m.group(1), i))

        m = SCENE.match(line)
        if m:
            scenes.append((m.group(1), i))

        # 引号成对（say 语句）
        if s.startswith('"') or (s.startswith('"') and s.count('"') >= 2):
            if s.count('"') % 2 != 0:
                problems.append("L%d 引号不成对: %s" % (i, s[:40]))
        elif re.match(r'^"[^"]*"\s+"', s):
            if s.count('"') % 2 != 0:
                problems.append("L%d 引号不成对: %s" % (i, s[:40]))

    # 1. label 唯一 + start
    names = [n for n, _ in labels]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        problems.append("label 重复: %s" % ", ".join(sorted(dup)))
    if "start" not in names:
        problems.append("缺少 label start")

    # 2. jump 目标存在
    for target, ln in jumps:
        if target not in names:
            problems.append("L%d jump 到未定义的 label: %s" % (ln, target))

    # 3. voice 文件存在
    for path, ln in voices:
        if not (game / path).exists():
            problems.append("L%d 语音文件缺失: %s" % (ln, path))

    # 4. scene bg 图片存在
    for bg, ln in scenes:
        cands = [game / "images" / "bg" / (bg + ".png"),
                 game / "images" / (bg + ".png"),
                 game / "images" / (bg + ".jpg")]
        if not any(c.exists() for c in cands):
            problems.append("L%d 背景图缺失: %s" % (ln, bg))

    # 4.5 非法/不约定的背景写法：scene expression "路径"
    #     Ren'Py 语法上能过，但和 images/bg/X.png 的约定对不上，运行时找不到图
    for i, line in enumerate(lines, 1):
        if re.match(r"^\s*scene\s+expression\b", line):
            problems.append(
                "L%d 用了 scene expression，应改成 scene bg <名字>（对应 images/bg/<名字>.png）" % i)

    # 4.6 一条 scene 都没有 = 上面的背景检查空转，必须报出来（否则是假阳性）
    if not scenes:
        problems.append("script.rpy 里没有任何 scene bg 指令 —— 背景检查没执行（假阳性），导出缺背景切换")

    # 5. menu 缩进
    for i, line in enumerate(lines, 1):
        if line.strip() == "menu:":
            base = len(line) - len(line.lstrip())
            nxt = lines[i] if i < len(lines) else ""
            if nxt.strip().startswith('"'):
                ind = len(nxt) - len(nxt.lstrip())
                if ind <= base:
                    problems.append("L%d menu 选项缩进不足" % (i + 1))

    print("工程: %s" % root)
    print("  script.rpy: %d 行" % len(lines))
    print("  labels: %d  jumps: %d  voice: %d  scene: %d" %
          (len(labels), len(jumps), len(voices), len(scenes)))
    print("-" * 60)

    if problems:
        print("发现 %d 个问题：" % len(problems))
        for p in problems:
            print("  [FAIL] " + p)
        return 1

    print("[PASS] 全部检查通过：label 闭环 / 跳转无悬空 / 资源齐全 / 缩进正确")
    return 0


if __name__ == "__main__":
    sys.exit(main())
