"""可独立校验的确定性模拟素材与剧情拓扑。"""
import io
import struct
import wave
import zlib
from datetime import datetime, timezone
from .engine.graph import edges_from_nodes

def png(width, height, color):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    raw = b''.join(b'\x00' + bytes(color) * width for _ in range(height))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')

def silent_wav():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(b'\x00\x00' * 24000)
    return buffer.getvalue()

def draft(project, body):
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
                 chars=[dict(id='heroine', pose='assets/char/mock.png', pos='center', mood='平静')],
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
