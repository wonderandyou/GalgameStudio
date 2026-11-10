# -*- coding: utf-8 -*-
"""把本机已有凭据灌进 GalgameStudio 的加密配置，省掉手工填 key。

来源：~/.dsh/.credentials.yaml（DSH 统一凭据）
目标：GalgameStudio/.state/credentials.json（Windows DPAPI 加密）

对应关系：
    qwen_image  ← DASHSCOPE_API_KEY   (百炼生图)
    bailian_tts ← DASHSCOPE_API_KEY   (百炼配音，同一把)
    llm         ← DEEPSEEK_API_KEY    (剧本)

★ 全程不打印任何 key 值，只报「写入了哪几项 + key 长度」。

跑：
    set GAL_STATE_DIR=D:\\软件\\GalgameStudio\\.state
    app\\.venv\\Scripts\\python.exe tools\\_seed_cloud_keys.py
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
sys.path.insert(0, str(ROOT))
os.environ.setdefault('GAL_STATE_DIR', str(ROOT / '.state'))

CRED = Path.home() / '.dsh' / '.credentials.yaml'

# 目标 kind → 来源变量名
MAPPING = {
    'qwen_image': 'DASHSCOPE_API_KEY',
    'bailian_tts': 'DASHSCOPE_API_KEY',
    'llm': 'DEEPSEEK_API_KEY',
}


def load_yaml_keys(path):
    """极简 YAML 读取：只取 `KEY: value` 形式的标量，够用且不引入依赖。"""
    out = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        if line.strip().startswith('#'):
            continue
        m = re.match(r'^\s*([A-Za-z0-9_]+)\s*:\s*(.+?)\s*$', line)
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        if value[:1] in ('"', "'"):
            value = value.strip('"\'')
        if value and value.lower() not in ('null', '~'):
            out[name] = value
    return out


def main():
    keys = load_yaml_keys(CRED)
    if not keys:
        print(f'[FAIL] 读不到凭据文件：{CRED}')
        return 2

    from app.store import Credentials

    patch = {}
    report = []
    for kind, var in MAPPING.items():
        value = keys.get(var)
        if not value:
            report.append(f'  {kind:14s} 跳过（{var} 没值）')
            continue
        patch[kind] = {'api_key': value, 'enabled': True}
        report.append(f'  {kind:14s} 写入（源 {var}，长度 {len(value)}）')

    if not patch:
        print('[FAIL] 没有可写入的 key')
        return 2

    print('=== 写入计划（不显示 key 值）===')
    for line in report:
        print(line)

    Credentials().write(patch)
    print('\n[OK] 已写入 .state/credentials.json（DPAPI 加密）')

    # 回读确认
    saved = Credentials().read()
    print('\n=== 回读确认 ===')
    for kind in MAPPING:
        cfg = saved.get(kind) or {}
        n = len(str(cfg.get('api_key') or ''))
        print(f'  {kind:14s} enabled={cfg.get("enabled")}  key长度={n}')

    # 重新算一遍 plan，看云端通道能不能用
    from app.providers import registry
    plan = registry.plan(saved)
    print('\n=== 通道计划 ===')
    for stage, prov in plan['providers'].items():
        if prov is None:
            print(f'  {stage:8s} -> 不可用')
        elif hasattr(prov, 'chain'):
            print(f'  {stage:8s} -> 回退链 {prov.chain()}')
        else:
            print(f'  {stage:8s} -> {prov.kind}（可用）')
    if plan['degraded']:
        print('  degraded:', plan['degraded'])
    else:
        print('  degraded: 无 —— 三个阶段的云端通道都齐了')
    return 0


if __name__ == '__main__':
    sys.exit(main())
