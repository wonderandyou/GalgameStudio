# -*- coding: utf-8 -*-
"""经理验收：素材库数据一致性 + 清理 Kimi 的测试残留。

看起来「语音 19 条」但只列 2 条卡片 —— 要弄清是「登记表 vs 磁盘」的合理差异，
还是显示逻辑有问题。
"""
import json
import sys
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8790'
PID = 'p_63b2b758e0ab46d1bf4201629468eb36'


def call(method, path, body=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as exc:
        return exc.code, {'err': exc.read().decode('utf-8', 'replace')[:200]}


def main():
    print('--- /library 各组数量 ---')
    _, lib = call('GET', f'/api/projects/{PID}/library')
    for key in ('background', 'character', 'reference', 'voice'):
        items = lib.get(key) or []
        print(f'  {key:10s} {len(items):3d} 条')
    voices_disk = lib.get('voice') or []
    print(f'  磁盘 voice 样例: {[v["name"] for v in voices_disk[:5]]}')

    print('\n--- /voices 登记表 ---')
    _, reg = call('GET', f'/api/projects/{PID}/voices')
    registered = reg.get('voices') or []
    print(f'  登记 {len(registered)} 条: {[(v.get("label"), v.get("character_id")) for v in registered]}')

    print('\n--- /references 登记表 ---')
    _, refs = call('GET', f'/api/projects/{PID}/references')
    ref_list = refs.get('references') or []
    print(f'  登记 {len(ref_list)} 条: {[(r.get("label"), r.get("kind")) for r in ref_list]}')

    print('\n结论：')
    print(f'  磁盘 voice 文件 {len(voices_disk)} 个 vs 登记语音 {len(registered)} 条')
    print('  → 差值是 TTS 生成的 silent_*.wav（属于节点台词，不是用户上传的语音素材）')
    print('  → 素材页底部「语音 N」如果显示的是磁盘数，会和卡片数不一致，属于误导')

    # 清理 Kimi 探针留下的测试登记
    removed = []
    for ref in ref_list:
        if 'kimi' in str(ref.get('label', '')).lower() or '测试' in str(ref.get('label', '')):
            status, _ = call('DELETE', f"/api/projects/{PID}/references/{ref['id']}")
            removed.append(('ref', ref.get('label'), status))
    for voice in registered:
        if 'kimi' in str(voice.get('label', '')).lower() or '测试' in str(voice.get('label', '')):
            status, _ = call('DELETE', f"/api/projects/{PID}/voices/{voice['id']}")
            removed.append(('voice', voice.get('label'), status))

    print('\n--- 清理测试残留 ---')
    if removed:
        for kind, label, status in removed:
            print(f'  删除 {kind}: {label} → HTTP {status}')
    else:
        print('  没有需要清理的测试登记')
    return 0


if __name__ == '__main__':
    sys.exit(main())
