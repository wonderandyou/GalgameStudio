"""端到端验证：素材库能不能让「第二个项目」白捡「第一个项目」生成的图。

这是省钱的最后一环。只看单元测试不够 —— 必须真的走一遍
`AgentTools.generate_image`，确认它先查库、命中就复制、不调用 provider。

跑：`app\\.venv\\Scripts\\python.exe tools\\_test_library_reuse.py`
"""
import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents.tools import AgentTools  # noqa: E402
from app.assets.library import Library, bg_key  # noqa: E402

PASS = []

# 64x36 的合法 PNG。假 provider 固定返回它，这样两次生成的字节必然相同。
PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000040000000240806000000'
    '16c1c8b30000000049454e44ae426082')


class FakeArt:
    """假的图像供应商：记录被调用了几次。

    这是整个测试的关键 —— 如果素材库生效，项目 B 的调用次数必须是 0。
    """
    kind = 'fake_art'

    def __init__(self):
        self.calls = 0

    async def call(self, prompt, width=1024, height=1024, timeout=180):
        self.calls += 1
        return PNG


def check(label, got, want):
    ok = got == want
    PASS.append(ok)
    print(f'{"✓" if ok else "✗"} {label}: {got!r}' + ('' if ok else f' (期望 {want!r})'))


def make_graph(place, time, weather, prompt, bg_key_value='tian_tai'):
    """两个节点共用同一个背景 key —— 和真实项目一样（25 个节点共用一个天台）。"""
    def node(nid):
        return dict(
            id=nid, type='scene', title=f'场景 {nid}', status='done',
            bg=dict(asset='', prompt=prompt, seed=0, provider='pending',
                    place=place, time=time, weather=weather),
            chars=[], lines=[dict(who='旁白', text='一句话。', voice=None, dur=1.0)],
            pos=dict(x=0, y=0), gen=dict(model='mock', at='2026-01-01T00:00:00+00:00', cost=0.0),
        )
    return dict(version=1,
                project=dict(id='p_' + '0' * 32, name='测试', resolution=[1280, 720],
                             created_at='2026-01-01T00:00:00+00:00'),
                characters=[], nodes=[node('n2'), node('n3')], edges=[],
                agent_state=dict(nodes={'n2': dict(bg_key=bg_key_value),
                                        'n3': dict(bg_key=bg_key_value)}))


async def run(project_dir, graph, library, provider):
    body = dict(params=dict(resolution=[1280, 720]))
    tools = AgentTools(graph, body, project_dir, providers={'art': provider}, library=library)
    result = await tools.generate_image(
        dict(target='background', key='tian_tai', prompt=graph['nodes'][0]['bg']['prompt']), scope='all')
    return tools, result


async def main():
    with TemporaryDirectory() as folder:
        base = Path(folder)
        library = Library(base / 'library')
        art = FakeArt()

        # ---- 项目 A：第一次，库里没有，应该真的生成 ----
        a = base / 'projects' / 'a'
        (a / 'assets').mkdir(parents=True)
        graph_a = make_graph('天台', '黄昏', '雨', '高楼天台，围栏，城市灯光，冷蓝色调')
        tools_a, result_a = await run(a, graph_a, library, art)
        check('项目A 生成了新图', result_a['generated'], True)
        check('项目A 没有复用', result_a.get('reused'), None)
        check('项目A 供应商被调用 1 次', art.calls, 1)
        check('项目A 库里有 1 个背景', library.stats()['bg'], 1)
        check('项目A 项目内资产存在', (a / result_a['asset']).is_file(), True)
        check('项目A 结构化字段写回节点',
              [n['bg']['place'] for n in tools_a.graph['nodes']], ['天台', '天台'])
        check('项目A 两个节点共用一张图',
              len({n['bg']['asset'] for n in tools_a.graph['nodes']}), 1)

        # ---- 项目 B：同一个地方、同一时间的另一种写法，必须精确命中 ----
        b = base / 'projects' / 'b'
        (b / 'assets').mkdir(parents=True)
        graph_b = make_graph('天台', '傍晚', '下雨', '学校天台，黄昏，远处车灯')
        tools_b, result_b = await run(b, graph_b, library, art)
        check('项目B 没有重新生图', result_b['generated'], False)
        check('项目B 精确命中', result_b.get('reused'), 'exact')
        check('项目B 没有多调一次供应商', art.calls, 1)
        check('项目B 用的是同一份字节', result_b['asset'], result_a['asset'])
        check('项目B 项目内资产存在', (b / result_b['asset']).is_file(), True)
        check('项目B 复用计数 +1', library.get(bg_key('天台', '黄昏', '雨'))['uses'], 1)

        # ---- 项目 B2：地点说法不同，应走相似命中，仍然不重新生成 ----
        b2 = base / 'projects' / 'b2'
        (b2 / 'assets').mkdir(parents=True)
        graph_b2 = make_graph('学校天台', '黄昏', '雨', '学校天台，栏杆，雨刚停')
        tools_b2, result_b2 = await run(b2, graph_b2, library, art)
        check('项目B2 没有重新生图', result_b2['generated'], False)
        check('项目B2 走相似命中', result_b2.get('reused', '').startswith('similar'), True)
        check('项目B2 供应商仍只调过 1 次', art.calls, 1)

        # ---- 项目 C：时间不同，必须重新生成 ----
        c = base / 'projects' / 'c'
        (c / 'assets').mkdir(parents=True)
        graph_c = make_graph('天台', '深夜', '雨', '深夜天台，只有远处一点灯')
        tools_c, result_c = await run(c, graph_c, library, art)
        check('项目C 时间不同必须重生成', result_c['generated'], True)
        check('项目C 供应商被调用 2 次', art.calls, 2)
        check('项目C 库里变成 2 个背景', library.stats()['bg'], 2)

        # ---- 预算闸门 ----
        ok, _ = library.within_budget(dict(bg=2, char=8, pose_per_char=12),
                                      extra=[bg_key('教室', '白天', '晴')])
        check('超预算时闸门拦住', ok, False)

        print()
        print(f'供应商总调用次数：{art.calls}（4 个项目只花了 2 次）')
        ok = all(PASS)
        print(f'{"全部通过" if ok else "有失败项"}（{sum(PASS)}/{len(PASS)}）')
        return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
