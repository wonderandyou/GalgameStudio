"""可独立校验的确定性模拟素材与剧情拓扑。"""
import io
import struct
import wave
import zlib
from datetime import datetime, timezone
from .engine.graph import edges_from_nodes

def png(width, height, color):
    """造一张纯色 PNG（mock 素材用，不依赖外部图片）。"""
    def chunk(kind, payload):
        """把列表按大小切块。"""
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    raw = b''.join(b'\x00' + bytes(color) * width for _ in range(height))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')

def silent_wav(seconds=1.0):
    """造一段静音 WAV（mock 语音用）。"""
    rate = 24000
    frames = max(1, round(seconds * rate))
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b'\x00\x00' * frames)
    return buffer.getvalue()

def skeleton(project):
    """新建项目的初始骨架：一个起点 + 一个结局 + 一条连线。

    ★ 不建空图的原因（实测踩到）：
      空图打开后是一块空画布，用户不知道从哪下手；更糟的是前端看到
      nodes 为空会直接提示"这个项目还没有节点"、根本进不去编辑器 ——
      新建完的项目等于死在那儿。
      给两个节点一条线，一眼就知道"剧情从这里往下接"。
    """
    stamp = datetime.now(timezone.utc).isoformat()

    def node(nid, kind, title, x, y, end_kind=None, text='（在这里写第一句台词）'):
        """造一个示例节点。"""
        return dict(
            id=nid, type=kind, title=title, status='done', end_kind=end_kind,
            bg=dict(asset='assets/bg/mock.png', prompt='占位背景，可随时替换',
                    seed=0, provider='mock'),
            chars=[dict(id='heroine', pose='assets/ref/mock.png',
                        pos='center', mood='平静', stance='stand')],
            lines=[dict(who='heroine', text=text, voice=None, dur=1.0)],
            pos=dict(x=x, y=y),
            gen=dict(model='manual', at=stamp, cost=0.0),
        )

    start = node('n1', 'start', '起点', 320, 220, text='（在这里写开场白）')
    start['choices'] = [dict(label='继续', to='n2', condition=None)]
    ending = node('n2', 'ending', '结局', 760, 220, end_kind='true',
                  text='（在这里写结局）')

    nodes = [start, ending]
    return dict(
        version=1,
        project=dict(project),
        characters=[dict(id='heroine', name='她', color='#d946ef',
                         ref_image='assets/ref/mock.png', voice_id='mock')],
        nodes=nodes,
        edges=edges_from_nodes(nodes),
    )


def draft(project, body):
    """造一份示例图（mock 项目用）。"""
    params = body['params']
    total = params['node_count']
    depth = min(params['branch_depth'], (total - 2) // 2)
    spine = total - depth - 1
    nodes = []
    for i in range(1, total + 1):
        ending = i > spine
        branching = not ending and depth > 0 and i > spine - depth
        kind = 'start' if i == 1 else 'ending' if ending else 'choice' if branching else 'scene'
        n = dict(id=f'n{i}', type=kind, title=f'模拟节点 {i}', status='pending', end_kind=('true' if i == total else 'bad') if ending else None,
                 bg=dict(asset='assets/bg/mock.png', prompt='M2 mock 纯色背景', seed=0, provider='mock'),
                 chars=[dict(id='heroine', pose='assets/ref/mock.png', pos='center', mood='平静', stance='stand')],
                 lines=[dict(who='heroine', text='雨还在下', voice=None, dur=1.0)],
                 pos=dict(x=(i - 1) * 320, y=320 if not ending else 80 + (i - spine) * 240),
                 gen=dict(model='mock', at=datetime.now(timezone.utc).isoformat(), cost=0.0))
        if not ending:
            n['choices'] = [dict(label='继续', to=f'n{i+1}' if i < spine else f'n{total}', condition=None)]
            if branching:
                side = spine + i - (spine - depth)
                n['choices'].append(dict(label='留下', to=f'n{side}', condition=None))
        nodes.append(n)
    project = dict(project, resolution=params['resolution'])
    return dict(version=1, project=project, characters=[dict(id='heroine', name='她', color='#d946ef', ref_image='assets/ref/mock.png', voice_id='mock')], nodes=nodes, edges=edges_from_nodes(nodes))
