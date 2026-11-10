# -*- coding: utf-8 -*-
"""统计所有生成任务的成功率和失败原因分布。

只读 .jobs/*.json，不改任何东西。
目的：从真实数据里找出「成功率低」到底是哪一类错误占大头，
而不是靠猜。
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
JOBS = ROOT / '.jobs'


def load_jobs():
    for f in sorted(JOBS.glob('*.json')):
        try:
            yield f, json.loads(f.read_text(encoding='utf-8'))
        except Exception:                                      # noqa: BLE001
            continue


def classify(text):
    """把错误信息归到几个大类（按出现顺序匹配，先具体后笼统）。"""
    if not text:
        return '（无错误信息）'
    rules = [
        ('判定卡死', '判定卡死（停滞检测）'),
        ('工具调用上限', '工具调用上限（旧逻辑，已移除）'),
        ('额度', '工具调用额度耗尽'),
        ('GRAPH_CYCLE', '剧情图有环'),
        ('有环', '剧情图有环'),
        ('选择节点', '分支数量不达标'),
        ('语音实际时长', '语音时长校验'),
        ('时长与字数', '语音时长校验'),
        ('缺少 voice', '缺语音'),
        ('AttributeError', '代码 bug（AttributeError）'),
        ('TypeError', '代码 bug（TypeError）'),
        ('KeyError', '代码 bug（KeyError）'),
        ('ValueError', '数据校验失败'),
        ('ProviderError', 'Provider 层错误'),
        ('HTTP', '网络/接口错误'),
        ('timeout', '超时'),
        ('Timeout', '超时'),
        ('超时', '超时'),
        ('取消', '用户取消'),
    ]
    for needle, label in rules:
        if needle in text:
            return label
    return '其他：' + text[:40]


def main():
    status = Counter()
    reasons = Counter()
    failed_detail = []
    total = 0
    for f, job in load_jobs():
        if job.get('kind') != 'generate':
            continue
        total += 1
        st = job.get('status') or '?'
        status[st] += 1
        if st in ('done', 'success'):
            continue
        err = ' '.join(str(x) for x in (
            job.get('error'), job.get('message'), job.get('failure')) if x)
        # 有些 error 藏在 logs 里的最后一条
        if not err:
            for k in ('logs', 'log', 'events'):
                items = job.get(k)
                if isinstance(items, list):
                    for it in reversed(items):
                        if isinstance(it, dict):
                            text = str(it.get('text') or it.get('msg') or '')
                            if 'error' in text.lower() or 'Error' in text:
                                err = text
                                break
                if err:
                    break
        label = classify(err)
        reasons[label] += 1
        failed_detail.append((f.name, st, label, err[:120]))

    print(f'=== 生成任务总数：{total} ===')
    print('\n状态分布：')
    for st, n in status.most_common():
        print(f'  {st:10s} {n:4d}  ({n/max(1,total)*100:.0f}%)')
    ok = status.get('done', 0) + status.get('success', 0)
    print(f'\n成功率 {ok}/{total} = {ok/max(1,total)*100:.1f}%')

    print('\n=== 失败原因分布 ===')
    for label, n in reasons.most_common():
        bar = '█' * min(40, n)
        print(f'  {n:4d}  {label:24s} {bar}')

    print('\n=== 最近 12 条失败明细 ===')
    for name, st, label, err in failed_detail[-12:]:
        print(f'  [{label}] {name[:22]}')
        print(f'      {err}')


if __name__ == '__main__':
    sys.exit(main())
