# -*- coding: utf-8 -*-
r"""把主人给的 key 写进 llm 配置。

★ 不走本地文件：`atomic_json` 写 %LOCALAPPDATA%\GalgameStudio 在这台机器上会卡住
（实测 12 秒不动，杀软锁目录），所以改成调后端自己的 `PUT /api/providers`，
让后端进程去做 DPAPI 加密落盘。key 从 docx 读，不进命令行、不打印。
"""
import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

BASE = 'http://127.0.0.1:8790'
DOCX = r'C:\Users\q3153\Desktop_new\新建 DOCX 文档.docx'


def read_key():
    with zipfile.ZipFile(DOCX) as z:
        xml = z.read('word/document.xml').decode('utf-8')
    text = re.sub(r'<[^>]+>', ' ', xml)
    text = re.sub(r'\s+', ' ', text).strip()
    found = re.search(r'sk-[A-Za-z0-9]{20,}', text)
    if not found:
        raise SystemExit('docx 里没找到 sk- 开头的 key')
    return found.group(0)


def call(method, path, payload=None, timeout=20):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode('utf-8')
    return json.loads(raw) if raw else None


def main():
    key = read_key()
    current = call('GET', '/api/providers')
    if not isinstance(current, dict) or 'llm' not in current:
        raise SystemExit('拿不到 providers 配置：%r' % (current,))

    body = {}
    for kind, cfg in current.items():
        body[kind] = {k: v for k, v in cfg.items()}
    body['llm']['api_key'] = key
    body['llm']['enabled'] = True

    saved = call('PUT', '/api/providers', body)
    check = call('GET', '/api/providers')
    masked = (check.get('llm') or {}).get('api_key')

    print('写入完成')
    print('  接口        : PUT /api/providers →', 'ok' if saved and saved.get('ok') else saved)
    print('  读回 api_key:', masked, '（后端只会返回 ***，无法比对明文）')
    print('  llm.enabled :', (check.get('llm') or {}).get('enabled'))
    print('  llm.base_url:', (check.get('llm') or {}).get('base_url'))
    print('  llm.model   :', (check.get('llm') or {}).get('model'))

    # 直接问后端要一次"这个 key 能不能用"——最实在的验证
    try:
        probe = call('POST', '/api/providers/llm/test', timeout=60)
        print('  连通性测试  :', json.dumps(probe, ensure_ascii=False)[:400])
    except urllib.error.HTTPError as exc:
        print('  连通性测试  : HTTP', exc.code, exc.read().decode('utf-8', 'replace')[:300])


if __name__ == '__main__':
    main()
