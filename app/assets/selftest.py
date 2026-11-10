"""素材库自测：key 归类、去重、预算、内容寻址。

跑：`app\\.venv\\Scripts\\python.exe -m app.assets.library`
"""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .library import (BUDGET, Library, bg_key, base_key, classify_time, classify_weather,
                      image_size, pose_key, similarity)

# 64x36 的最小合法 PNG（与 engine 里占位图同源），用来验证内容寻址。
PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000040000000240806000000'
    '16c1c8b30000000049454e44ae426082')


def check(label, got, want):
    ok = got == want
    print(f'{"✓" if ok else "✗"} {label}: {got!r}' + ('' if ok else f' (期望 {want!r})'))
    return ok


def main():
    passed = True

    # 1. 地点/时间/天气拆解 —— 这是省图的地基
    passed &= check('黄昏的天台 → 地点', bg_key('黄昏的天台'), 'bg:天台|dusk|clear')
    passed &= check('天台的黄昏 → 地点', bg_key('天台的黄昏'), 'bg:天台|dusk|clear')
    passed &= check('同一地点同一时间 → 同 key',
                    bg_key('黄昏的天台') == bg_key('天台的黄昏'), True)
    passed &= check('白天 vs 夜晚必须不同',
                    bg_key('夜晚的天台') != bg_key('黄昏的天台'), True)
    passed &= check('雨天 vs 晴天必须不同',
                    bg_key('雨天的天台') != bg_key('晴天的天台'), True)
    passed &= check('时间归一类', classify_time('傍晚的走廊'), 'dusk')
    passed &= check('天气归类', classify_weather('下着小雨'), 'rain')

    # 2. 姿势/表情枚举：自由文本必须收敛
    passed &= check('姿势枚举收敛', pose_key('heroine', '坐着', '微笑'),
                    'char:heroine:sit:smile')
    passed &= check('表情枚举收敛', pose_key('heroine', '站', '微微一笑'),
                    'char:heroine:stand:smile')
    passed &= check('认不出的表情回落中性', pose_key('heroine', 'stand', '平静'),
                    'char:heroine:stand:neutral')
    passed &= check('同一角色同一姿势 → 同 key',
                    pose_key('heroine', '坐着', '微笑') == pose_key('heroine', 'sit', 'smile'), True)
    passed &= check('基底 key', base_key('heroine'), 'char:heroine:base')

    # 3. 相似度：「学校天台」应能命中「天台」，但「天台」不能命中「教室」
    passed &= check('学校天台 ≈ 天台', similarity('学校天台', '天台') > 0.5, True)
    passed &= check('天台 ≉ 教室', similarity('天台', '教室') < 0.5, True)

    # 4. 内容寻址 + 查库 + 复用计数
    with TemporaryDirectory() as folder:
        lib = Library(folder)
        passed &= check('空库背景数', lib.stats()['bg'], 0)
        entry = lib.put(bg_key('黄昏的天台'), PNG, prompt='黄昏的天台，夕阳',
                        provider='mock', method='txt2img', seed=7)
        passed &= check('写入后背景数', lib.stats()['bg'], 1)
        passed &= check('尺寸从 PNG 头读出', image_size(PNG), (64, 36))
        passed &= check('文件落盘存在', (Path(folder) / entry['file']).is_file(), True)

        # 同一份字节用另一个 key 再存一次：磁盘上只能有一份文件
        lib.put(bg_key('夜晚的天台'), PNG, prompt='夜晚的天台', provider='mock')
        files = list((Path(folder) / 'files').rglob('*.png'))
        passed &= check('相同字节只落一份盘', len(files), 1)
        passed &= check('不同时间算不同素材', lib.stats()['bg'], 2)

        # 查库：精确命中
        key, found, how = lib.find_bg('黄昏的天台')
        passed &= check('精确命中', how, 'exact')
        passed &= check('命中返回 key', key, bg_key('黄昏的天台'))
        # 查库：近似地点 + 同时间，应命中已有的
        key, found, how = lib.find_bg('学校天台', time='黄昏')
        passed &= check('近似地点命中', (bool(found), how.startswith('similar')), (True, True))
        # 查库：时间不同，不许复用
        key, found, how = lib.find_bg('学校天台', time='早上')
        passed &= check('时间不同不复用', (key, found), (None, None))

        # 复用计数
        lib.bump(bg_key('黄昏的天台'))
        lib.bump(bg_key('黄昏的天台'))
        passed &= check('复用计数', lib.get(bg_key('黄昏的天台'))['uses'], 2)

        # 索引落盘后重开应读回
        again = Library(folder)
        passed &= check('重开库读回条目', len(again.entries), 2)

        # 5. 预算闸门
        ok, why = lib.within_budget(dict(BUDGET, bg=2), extra=[bg_key('雨天的天台')])
        passed &= check('超预算被拦', (ok, why), (False, f'背景素材已达上限 {2} 张'))
        ok, why = lib.within_budget(dict(BUDGET, bg=2), extra=[bg_key('夜晚的天台')])
        passed &= check('已存在的素材不重复占预算', ok, True)
        ok, why = lib.within_budget(dict(BUDGET, bg=99))
        passed &= check('预算内放行', ok, True)

    print()
    print('全部通过' if passed else '有失败项')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
