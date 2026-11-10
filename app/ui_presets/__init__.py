"""界面风格预设：一套预设 = 一份可渲染的界面视觉数据。

为什么要有预设
--------------
界面最容易崩的地方不是「做不出来」，而是**每做一个界面就换一套视觉语言** ——
启动页是水墨、对话框是亮蓝圆角、存档页又是暗色毛玻璃，玩家一眼就觉得像拼的。

所以界面的正确做法和美术一样：**先选预设，再渲染**。
预设把「配色 / 字体 / 排版骨架 / 六块皮的做法」固化下来，
agent 只负责「按关键词挑一套 + 少量微调」，不许从零自由发挥。

数据形态
--------
每个 `*.json` 一份预设，字段见 `REQUIRED`。配色和排版是**参数**，
具体 CSS 在 `render.py` 里按 id 分派（四套的骨架差异太大，参数化反而更难维护）。

关键词匹配
----------
`match(text)` 做加权打分，返回可解释的排序结果：
命中词越具体、越靠前，权重越高。同一分值时按 `order` 稳定排序。
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

REQUIRED = ('id', 'name', 'vibe', 'keywords', 'fit', 'palette', 'typeface', 'layout')

# 预设展示顺序（也是同分时的排位）
ORDER = ('ink-classic', 'cinema-dark', 'paper-indie', 'theater-cold')

_cache = {}


def _load_all():
    """加载所有界面预设并缓存到进程内。

    两类来源：
    - 仓库自带的四套（本目录 `*.json`）；
    - 用户按要求和参考图设计出来的（`designed/*.json`，见 `design.py`）。

    `designed/` 不存在就当没有 —— 干净 clone 下来不该因为少个目录就导入失败。
    """
    if _cache:
        return _cache
    paths = sorted(HERE.glob('*.json'))
    designed = HERE / 'designed'
    if designed.is_dir():
        paths += sorted(designed.glob('*.json'))
    for path in paths:
        data = json.loads(path.read_text(encoding='utf-8'))
        missing = [key for key in REQUIRED if key not in data]
        if missing:
            raise ValueError(f'{path.name} 缺字段 {missing}')
        _cache[data['id']] = data
    return _cache


def all_presets():
    """按展示顺序返回全部预设。"""
    presets = _load_all()
    ordered = [presets[i] for i in ORDER if i in presets]
    return ordered + [p for key, p in presets.items() if key not in ORDER]


def get(preset_id):
    """按 id 取一个预设；不存在返回 None。"""
    preset = _load_all().get(preset_id)
    if preset is None:
        raise KeyError(f"没有这个界面预设：{preset_id}（可用：{', '.join(_load_all())}）")
    return preset


def brief(preset):
    """给 agent / 前端看的精简视图（不含排版细节）。"""
    return dict(
        id=preset['id'], name=preset['name'], vibe=preset['vibe'],
        fit=list(preset['fit']), keywords=list(preset['keywords'])[:8],
        palette=dict(preset['palette']), menu=preset['layout'].get('menu', ''),
    )


def match(text, limit=4):
    """按关键词给预设打分。

    返回 `[{id, name, score, hits, reason}]`，score 已归一化到 0~1。
    没有命中任何词时返回空列表 —— 让调用方明确知道「这套关键词没人接」，
    而不是硬塞一个默认值（硬塞会让界面和故事对不上）。
    """
    text = str(text or '').strip().lower()
    if not text:
        return []
    presets = all_presets()
    preset_words = {p['id']: [str(w).lower() for w in p['keywords'][:5]] for p in presets}
    scored = []
    for index, preset in enumerate(presets):
        hits, points = [], 0
        for weight, words in ((12, preset['keywords'][:5]), (9, preset['keywords'][5:]),
                              (7, preset['fit'])):
            for word in words:
                word = str(word).lower()
                if word and word in text:
                    if word not in hits:
                        hits.append(word)
                        points += weight
        # 风格自身名字/气质里出现的描述词也算弱命中
        for word in (preset['name'], preset['vibe']):
            if str(word).lower() in text:
                points += 5
        if points:
            scored.append(dict(id=preset['id'], name=preset['name'], points=points,
                               hits=hits, order=index))
    if not scored:
        return []
    top = max(item['points'] for item in scored)
    for item in scored:
        item['score'] = round(item['points'] / top, 3)
        item['reason'] = '、'.join(item['hits']) or '名称/气质命中'
        # 只沾到一个边缘词就说"完全匹配"会误导 agent —— 标出匹配置信度：
        # strong = 命中 2 个词以上，或命中了前 5 个主关键词；
        # weak   = 只沾到 1 个边缘词，气质很可能不对，必须让用户确认。
        first_five = set(preset_words.get(item['id'], ()))
        primary_hit = any(word in first_five for word in item['hits'])
        item['strength'] = 'strong' if (len(item['hits']) >= 2 or primary_hit) else 'weak'
        item.pop('points')
        item.pop('order')
    scored.sort(key=lambda item: (-item['score'], [p['id'] for p in presets].index(item['id'])))
    return scored[:limit]


def check():
    """自检：字段齐全、id 唯一、配色合法、关键词不为空。返回问题列表。"""
    problems = []
    seen = set()
    for path in sorted(HERE.glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            problems.append(f'{path.name}: JSON 解析失败 {exc}')
            continue
        missing = [key for key in REQUIRED if key not in data]
        if missing:
            problems.append(f'{path.name}: 缺字段 {missing}')
            continue
        if data['id'] in seen:
            problems.append(f"{path.name}: id 重复 {data['id']}")
        seen.add(data['id'])
        if data['id'] != path.stem:
            problems.append(f"{path.name}: 文件名与 id({data['id']}) 不一致")
        if not data['keywords']:
            problems.append(f"{path.name}: keywords 为空，agent 无法匹配")
        if not data['fit']:
            problems.append(f"{path.name}: fit 为空，告诉不了 agent 什么时候用它")
        for field in ('bg', 'fg', 'accent'):
            value = str((data['palette'] or {}).get(field) or '')
            if not value.startswith('#') or len(value) not in (7, 9):
                problems.append(f"{path.name}: palette.{field} 不是 #RRGGBB（{value!r}）")
        for field in ('title', 'body'):
            if not str((data['typeface'] or {}).get(field) or '').strip():
                problems.append(f"{path.name}: typeface.{field} 为空")
        layout = data['layout'] or {}
        if layout.get('menu') not in ('vertical', 'horizontal', 'numbered'):
            problems.append(f"{path.name}: layout.menu={layout.get('menu')!r} 不是 vertical/horizontal/numbered")
    for preset_id in ORDER:
        if preset_id not in seen:
            problems.append(f'缺少预设 {preset_id}')
    return problems


# 渲染函数从子模块导出到包级：`from app.ui_presets import render` 拿到的是函数，
# 而不是同名的 render 模块（踩过：模块与函数同名，import 到的是模块）。
from .render import SCREENS, render, write  # noqa: E402,F401

# 设计能力：要求文字 + 参考图 → 一套新的预设（见 design.py）。
# 同样必须导出**函数**：`design` 既是模块名又是函数名，只 `import design` 会拿到模块，
# 调用时才会发现「module object is not callable」，报错点离真因很远。
from .design import (  # noqa: E402,F401
    DESIGNED_DIR, MAX_REFERENCES, MAX_REFERENCE_BYTES,
    analyze as analyze_design,
    compose as compose_design,
    design as run_design,
    palette_from_images,
    save as save_design,
)


if __name__ == '__main__':
    issues = check()
    if issues:
        print(f'✗ {len(issues)} 处问题')
        for issue in issues:
            print('  -', issue)
        raise SystemExit(1)
    presets = all_presets()
    print(f'✓ {len(presets)} 套界面预设')
    for preset in presets:
        print(f"  {preset['id']:14s} {preset['name']:10s} 菜单={preset['layout']['menu']:10s} "
              f"关键词={len(preset['keywords'])} 个")
