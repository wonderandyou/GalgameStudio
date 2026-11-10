# -*- coding: utf-8 -*-
"""把本地工作区同步到 GitHub（wonderandyou/GalgameStudio）。

为什么不直接 git push 原仓库
--------------------------
原仓库历史 348 MB，而且历史里含过 Edge 的 Vpn Tokens 文件。
直接 push 会把这些一起带上 GitHub。所以：
  本地保留完整历史  →  发布走一个干净的独立仓库（只含当前代码快照）

用法
----
    python tools/sync_to_github.py                      # 自动写 commit message
    python tools/sync_to_github.py -m "fix: xxx"        # 指定 message
    python tools/sync_to_github.py -n                   # 只看会改什么，不提交不推

退出码：0 = 已同步或无需同步；1 = 有失败
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLISH = Path('D:/软件/_gal-publish')     # 发布用的干净仓库（在项目目录外）
REMOTE = 'https://github.com/wonderandyou/GalgameStudio.git'
# 只同步「源码」。docs/、HANDOFF.md、提示词、日志、产物一律不进仓库
# （.gitignore 里有对应规则兜底，这里是双保险 + 明确意图）
SYNC_DIRS = ['app', 'ui', 'launcher', 'tools', 'tests', 'scripts', 'unity',
             'requirements.txt', 'README.md']
GITATTR = (
    '* text=auto eol=lf\n'
    '*.png binary\n*.jpg binary\n*.jpeg binary\n'
    '*.wav binary\n*.mp3 binary\n*.onnx binary\n'
    '*.exe binary\n*.dll binary\n'
)


def run(args, cwd=None, check=False):
    r = subprocess.run(args, cwd=str(cwd or ROOT), capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} 失败:\n{r.stdout}\n{r.stderr}")
    return r


def say(tag, msg):
    colors = {'1': '\033[36m', '2': '\033[32m', '3': '\033[33m', 'E': '\033[31m', '0': '\033[90m'}
    print(f"{colors.get(tag, '')}{msg}\033[0m")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-m', '--message', default='')
    ap.add_argument('-n', '--dry-run', action='store_true')
    args = ap.parse_args()

    # ---------- 1. 本地提交 ----------
    st = run(['git', 'status', '--porcelain', '--'] + SYNC_DIRS)
    dirty = [ln for ln in st.stdout.splitlines() if ln.strip()]
    if not dirty:
        say('0', '[1/4] 本地无源码改动')
    else:
        say('1', f'[1/4] 本地改动 {len(dirty)} 项：')
        for ln in dirty[:15]:
            print('      ' + ln)
        if len(dirty) > 15:
            print(f'      … 另有 {len(dirty) - 15} 项')
        message = args.message or f"sync: {datetime.now():%Y-%m-%d %H:%M} 同步本地改动"
        if args.dry_run:
            say('3', '      (dry-run：不提交)')
        else:
            run(['git', 'add', '-A', '--'] + SYNC_DIRS, check=True)
            run(['git', 'commit', '-q', '-m', message], check=True)
            say('2', f'      已本地提交：{message}')
    if args.dry_run:
        say('3', 'dry-run 结束')
        return 0

    # ---------- 2. 克隆 / 清空发布仓库 ----------
    if not (PUBLISH / '.git').exists():
        say('1', '[2/4] 首次运行，克隆发布仓库…')
        if PUBLISH.exists():
            import shutil
            shutil.rmtree(PUBLISH, ignore_errors=True)
        run(['git', 'clone', REMOTE, str(PUBLISH)], cwd=Path('D:/软件'), check=True)

    import shutil
    for item in PUBLISH.iterdir():
        if item.name == '.git':
            continue
        shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink()

    # ---------- 3. 从本地索引导出快照 ----------
    run(['git', f'--work-tree={PUBLISH}', 'checkout-index', '-a', '-f'], check=True)
    n = sum(1 for p in PUBLISH.rglob('*') if p.is_file() and '.git' not in p.parts)
    say('1', f'[2/4] 已导出 {n} 个文件')

    # ---------- 4. 发布仓库提交 + 推送 ----------
    run(['git', 'config', 'user.name', 'wonderandyou'], cwd=PUBLISH)
    run(['git', 'config', 'user.email', 'wonderandyou@users.noreply.github.com'], cwd=PUBLISH)
    attr = PUBLISH / '.gitattributes'
    if not attr.exists():
        attr.write_text(GITATTR, encoding='ascii')

    run(['git', 'add', '-A'], cwd=PUBLISH, check=True)
    staged = run(['git', 'diff', '--cached', '--name-only'], cwd=PUBLISH).stdout.splitlines()
    if not staged:
        say('0', '[3/4] 发布仓库无变化，跳过提交')
    else:
        message = args.message or f"sync: {datetime.now():%Y-%m-%d %H:%M}"
        run(['git', 'commit', '-q', '-m', message], cwd=PUBLISH, check=True)
        say('2', f'[3/4] 发布仓库提交 {len(staged)} 个文件')

    if not (run(['git', 'remote'], cwd=PUBLISH).stdout or '').strip():
        run(['git', 'remote', 'add', 'origin', REMOTE], cwd=PUBLISH, check=True)

    say('1', '[4/4] 推送中…')
    push = run(['git', 'push', 'origin', 'main'], cwd=PUBLISH)
    blob = (push.stdout + push.stderr)
    if push.returncode == 0 or 'up-to-date' in blob:
        say('2', '✅ 已同步：https://github.com/wonderandyou/GalgameStudio')
        return 0
    say('E', '❌ 推送失败：')
    print('\n'.join(blob.splitlines()[-12:]))
    return 1


if __name__ == '__main__':
    sys.exit(main())
