# -*- coding: utf-8 -*-
"""用 agent_state.skeleton 的拓扑修复被前端保存弄坏的 graph.json。

背景
----
旧版前端保存（ui/app.js:912 的章节合并无去重）会把 edges 和 choices 反复追加，
一张原本合法的图会变成「start 有 27 条出边 / choice 有 26 个选项 / 32 个 scene 断头」。

但 `agent_state.skeleton` 存着生成时的**原始拓扑**（每个节点 `to: [ref...]`），
而且 `agent_state.nodes` 有 `{nid: {ref: "k001", ...}}` 的完整映射。
所以不需要重新调用模型 —— 用骨架的拓扑 + 现有节点的内容（台词/背景/立绘/配音），
就能把图原地修好，**内容零损失**。

修复做了什么
-----------
对每个节点：
  - 用 `agent_state.nodes[nid].ref` 找到对应 skeleton 节点
  - 用 skeleton 的 `to` 列表重建 `choices[].to`
  - `choices[].label` 优先沿用旧值（按 port 下标），缺失时给默认值
  - `edges` 一律由 `choices` 重算

默认只打印（dry-run）。加 `--apply` 才写盘：先过 apply_graph 严格闸，
通过后自动备份到 `_backup_projects/<时间戳>/`，再原子写回。

核心逻辑在 `app/engine/repair.py`（和 checkpoint 恢复、启动自检、API 共用）；
本脚本只负责扫描、打印和命令行参数，用法不变。

用法
----
    python tools/repair_from_skeleton.py                 # 只扫描+报告
    python tools/repair_from_skeleton.py --apply         # 修复所有需要修的项目
    python tools/repair_from_skeleton.py --apply --only p_04927612e8c44ec0b5519223a2a464e5
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine.graph import GraphRejected, normalize_graph, structure_problems  # noqa: E402
from app.engine.repair import has_skeleton, repair_project                       # noqa: E402

PROJECTS = ROOT / 'projects'
BACKUP = ROOT / '_backup_projects'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真的写盘（默认只报告）')
    ap.add_argument('--only', default=None, help='只处理某个项目 id')
    ap.add_argument('--projects', default=str(PROJECTS), help=argparse.SUPPRESS)
    ap.add_argument('--backup', default=str(BACKUP), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    targets = sorted(Path(args.projects).glob('*/graph.json'))
    if args.only:
        targets = [p for p in targets if p.parent.name == args.only]

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = Path(args.backup) / stamp
    todo = []
    for path in targets:
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:
            print('跳过 %s：读取失败 %s' % (path.parent.name, exc))
            continue
        nodes = doc.get('nodes') or []
        if not nodes:
            continue
        before = structure_problems(normalize_graph(doc))
        if not before:
            continue
        todo.append(dict(path=path, pid=path.parent.name, doc=doc,
                         before=before, has_sk=has_skeleton(doc)))

    print('待修项目 %d 个（有 skeleton 的 %d 个）\n'
          % (len(todo), sum(1 for t in todo if t['has_sk'])))
    print('%-36s %5s %7s %6s %7s %8s %s'
          % ('项目', '节点', '修复前', '修复后', '沿用lbl', '可见丢失', '结论'))
    print('-' * 96)
    fixed = 0
    for t in todo:
        if not t['has_sk']:
            print('%-36s %5d %7d %6s %7s %8s %s'
                  % (t['pid'], len(t['doc']['nodes']), len(t['before']), '-', '-', '-',
                     '无 skeleton，跳过'))
            continue
        try:
            info = repair_project(t['path'].parent, backup_root=args.backup, apply=args.apply,
                                  source='tools.repair_from_skeleton', stamp=stamp)
        except GraphRejected as exc:
            print('%-36s %5d %7d %6s %7s %8s %s'
                  % (t['pid'], len(t['doc']['nodes']), len(t['before']), len(exc.problems), '-', '-',
                     '✗ 严格闸拒绝，未写盘'))
            continue
        if 'problems_after' not in info:
            print('%-36s %5d %7d %6s %7s %8s %s'
                  % (t['pid'], len(t['doc']['nodes']), len(t['before']), '-', '-', '-',
                     info.get('reason')))
            continue
        after = info['problems_after']
        verdict = '✓ 可修' if not after else '✗ 仍剩 %d' % after
        print('%-36s %5d %7d %6d %7d %8d %s'
              % (t['pid'], len(t['doc']['nodes']), len(t['before']), after,
                 info['kept'], len(info['lost_visible']), verdict))
        if not after:
            fixed += 1

    print('\n可修复 %d 个；%s' % (fixed,
                                 ('已写盘 %d 个，备份在 %s' % (fixed, backup_dir))
                                 if args.apply else '这是 dry-run，加 --apply 才写盘'))
    if not args.apply:
        print('提示：projects/ 不在 git 里（.gitignore 已排除），改之前一定要 --apply 让它自动备份。')


if __name__ == '__main__':
    sys.exit(main() or 0)
