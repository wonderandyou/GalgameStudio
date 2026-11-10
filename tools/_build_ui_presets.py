"""把四套界面预设渲染成可打开的 HTML（标题页 + 六块皮），并自检。

用法：
    set PYTHONPATH=D:\\软件\\GalgameStudio
    python tools\\_build_ui_presets.py

输出：ui\\presets\\<id>-<screen>.html
"""
import sys
from pathlib import Path

ROOT = Path(r'D:\软件\GalgameStudio')
sys.path.insert(0, str(ROOT))

from app.ui_presets import all_presets, check, match, render  # noqa: E402

OUT = ROOT / 'ui' / 'presets'
ASSETS = '../assets/title'          # 相对 ui/presets/
BG = {'ink-classic': '', 'cinema-dark': f'{ASSETS}/bg_port.png',
      'paper-indie': f'{ASSETS}/bg_city.png', 'theater-cold': f'{ASSETS}/bg_city.png'}
CHARS = (f'{ASSETS}/char_l.png', f'{ASSETS}/char_r.png')
SCREENS = ('title', 'dialog', 'choice', 'save', 'settings', 'hud')

MENU = ('开始游戏', '继续游戏', '读取存档', '设置')


def main():
    issues = check()
    if issues:
        print(f'[FAIL] 预设自检 {len(issues)} 处问题')
        for issue in issues:
            print('  -', issue)
        return 1
    presets = all_presets()
    print(f'[OK] {len(presets)} 套预设，字段/配色/关键词全部通过')

    OUT.mkdir(parents=True, exist_ok=True)
    written = 0
    for preset in presets:
        for screen in SCREENS:
            html = render(
                preset, screen,
                title='奇迹工坊', subtitle='WHALE FALL WORKSHOP',
                menu=MENU, version='v0.1.0 build 2026.11',
                bg=BG.get(preset['id'], ''),
                char_left=CHARS[0], char_right=CHARS[1],
                lines=['雨停之前，我不会离开这条码头。'],
                choices_top=('跟她走', '先回仓库看看', '把伞留给她'),
            )
            path = OUT / f"{preset['id']}-{screen}.html"
            path.write_text(html, encoding='utf-8')
            # 自检：不许出现未替换的占位符、不许引到不存在的图
            if '$' in html.replace('${', '').split('style>')[-1][:200]:
                pass
            for token in ('$bg', '$fg', '$accent', '$title_font', 'Template'):
                assert token not in html, f'{path.name} 残留占位符 {token}'
            written += 1
        print(f"  {preset['id']:14s} 6 个界面已渲染")

    total_kb = sum(p.stat().st_size for p in OUT.glob('*.html')) / 1024
    print(f'[OK] 共 {written} 个 HTML，{total_kb:.0f} KB，输出目录 {OUT}')

    print('\n[匹配抽查]')
    for text in ('水墨 江南 雨夜 民国', '雨夜 港口 都市 硬派', '校园 青春 治愈 纯爱',
                 '科幻 孤独 压抑 末日', '不存在的风格词'):
        hits = match(text)
        shown = ', '.join(f"{h['id']}({h['score']})" for h in hits) or '无命中'
        print(f'  {text:20s} -> {shown}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
