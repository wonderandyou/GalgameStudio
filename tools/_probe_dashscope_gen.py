"""探针：验证 DASHSCOPE key 能否真调百炼生图（qwen-image）。

用法：
    D:\\软件\\GalgameStudio\\app\\.venv\\Scripts\\python.exe tools\\_probe_dashscope_gen.py

凭据读取顺序：环境变量 DASHSCOPE_API_KEY → ~/.dsh/.credentials.yaml
绝不打印 key 原文（只打印长度与前缀 3 位）。
"""
import base64
import json
import os
import pathlib
import re
import sys
import time

import httpx

CRED = pathlib.Path.home() / '.dsh' / '.credentials.yaml'
OUT = pathlib.Path(__file__).with_name('_probe_gen_out.png')


def load_key():
    env = os.environ.get('DASHSCOPE_API_KEY', '').strip()
    if env:
        return env, 'env'
    if not CRED.exists():
        return '', f'凭据文件不存在: {CRED}'
    text = CRED.read_text(encoding='utf-8', errors='replace')
    # 放宽：取冒号后整段（去引号/空白），不限制字符集
    for line in text.splitlines():
        m = re.match(r'^\s*DASHSCOPE_API_KEY\s*:\s*(.+?)\s*$', line)
        if m:
            val = m.group(1).strip().strip('"\'')
            if val:
                return val, 'credentials.yaml'
    return '', 'credentials.yaml 里没找到 DASHSCOPE_API_KEY'


def main():
    key, src = load_key()
    if not key:
        print(f'[FAIL] 取不到 key：{src}')
        return 2
    print(f'[OK] key 已读到（来源={src}，长度={len(key)}，前缀={key[:3]}***）')

    prompt = ('galgame 标题界面用的背景素材，雨夜港口的青石台阶，远处一串暖黄灯笼，'
              '赛璐璐平涂，细描线，色块干净，冷蓝主调配暖黄点缀，无人，无文字，留白构图')
    payload = {
        'model': 'qwen-image-3.0-pro',
        'input': {'messages': [{'role': 'user', 'content': [{'text': prompt}]}]},
        'parameters': {'size': '1280*720', 'n': 1, 'prompt_extend': True, 'watermark': False},
    }
    url = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}

    t0 = time.time()
    try:
        with httpx.Client(timeout=httpx.Timeout(240, connect=15), trust_env=False) as c:
            r = c.post(url, json=payload, headers=headers)
    except Exception as exc:                                   # noqa: BLE001
        print(f'[FAIL] 网络异常：{type(exc).__name__}: {exc}')
        return 3

    dt = time.time() - t0
    print(f'[HTTP] {r.status_code}  耗时 {dt:.1f}s')
    if r.status_code != 200:
        body = r.text.replace(key, '***')[:1200]
        print(f'[FAIL] 响应非 200：{body}')
        return 4

    body = r.json()
    # 响应形状：output.choices[0].message.content[] 里有一项 image=url
    try:
        contents = body['output']['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        print('[FAIL] 响应结构异常：' + json.dumps(body, ensure_ascii=False)[:1200])
        return 5

    img_url = ''
    for item in contents:
        if isinstance(item, dict) and item.get('image'):
            img_url = item['image']
            break
    if not img_url:
        print('[FAIL] content 里没有 image 字段：' + json.dumps(contents, ensure_ascii=False)[:800])
        return 6

    print(f'[OK] 拿到图 URL（{img_url[:60]}...）')
    try:
        with httpx.Client(timeout=httpx.Timeout(120, connect=15), trust_env=False) as c:
            img = c.get(img_url)
        img.raise_for_status()
    except Exception as exc:                                   # noqa: BLE001
        print(f'[FAIL] 下图失败：{type(exc).__name__}: {exc}')
        return 7

    data = img.content
    print(f'[OK] 图字节 {len(data)}，魔数 {data[:4]!r}')
    if not (data.startswith(b'\x89PNG') or data.startswith(b'\xff\xd8')):
        print('[FAIL] 不是 PNG/JPEG')
        return 8
    OUT.write_bytes(data)
    print(f'[PASS] 已存 {OUT}  ({len(data)/1024:.1f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
