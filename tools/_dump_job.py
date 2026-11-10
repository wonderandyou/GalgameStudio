# -*- coding: utf-8 -*-
"""把一个 job 的 SSE 事件流 dump 出来（只打 agent / log / error 类）。"""
import sys

import httpx

JOB = sys.argv[1]
BASE = 'http://127.0.0.1:8790'

with httpx.stream('GET', f'{BASE}/api/jobs/{JOB}/events', timeout=8.0) as r:
    for line in r.iter_lines():
        if not line.startswith('data:'):
            continue
        text = line[5:].strip()
        if '"agent"' in text or '"level"' in text or '"code"' in text:
            print(text[:260])
