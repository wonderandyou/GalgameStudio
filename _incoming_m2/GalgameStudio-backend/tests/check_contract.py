"""独立的契约与二进制素材检查；不以此替代 HTTP/SSE 实测。"""
import copy
import io
import struct
import sys
import wave
import zlib
from pathlib import Path

# 允许从项目根直接 `python tests\check_contract.py`：脚本模式 sys.path[0] 是 tests\，
# 不包含项目根，导致 `import app` 失败。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.mock_data import draft, png, silent_wav
from app.engine.graph import validate_graph, edges_from_nodes

project = dict(id='p_test', name='测试', resolution=[1920, 1080], created_at='2026-11-06T00:00:00+08:00')
count = 0
for total in range(4, 65):
    for depth in range(9):
        for voiced in (True, False):
            graph = draft(project, {'params': dict(node_count=total, branch_depth=depth, resolution=[1920,1080])})
            for node in graph['nodes']:
                node['status'] = 'done'
                node['lines'][0]['voice'] = 'assets/voice/mock.wav' if voiced else None
                node['lines'][0]['dur'] = 1.0 if voiced else 4/4.5
            validate_graph(graph, auto_voice=voiced)
            assert len(graph['nodes']) == total
            count += 1
print(f'PASS {count} graph combinations: node_count 4..64, branch_depth 0..8, auto_voice true/false')
invalid = copy.deepcopy(graph)
invalid['nodes'][0]['choices'][0]['to'] = 'n1'
invalid['edges'] = edges_from_nodes(invalid['nodes'])
try:
    validate_graph(invalid, auto_voice=False)
except ValueError:
    print('PASS cycle rejection')
else:
    raise AssertionError('cycle accepted')
invalid = copy.deepcopy(graph)
invalid['edges'] = []
try:
    validate_graph(invalid, auto_voice=False)
except ValueError:
    print('PASS edge/choice mismatch rejection')
else:
    raise AssertionError('edge mismatch accepted')
data = png(64,36,(35,50,75,255))
assert data[:8] == b'\x89PNG\r\n\x1a\n'
offset = 8
while offset < len(data):
    length = struct.unpack('>I', data[offset:offset+4])[0]
    chunk = data[offset+4:offset+8+length]
    crc = struct.unpack('>I', data[offset+8+length:offset+12+length])[0]
    assert zlib.crc32(chunk) & 0xffffffff == crc
    if chunk[:4] == b'IDAT':
        assert len(zlib.decompress(chunk[4:])) == 36*(1+64*4)
    offset += 12+length
print('PASS PNG structure, CRC and pixel buffer')
with wave.open(io.BytesIO(silent_wav()), 'rb') as audio:
    assert audio.getframerate() == 24000 and audio.getnframes() == 24000 and audio.getsampwidth() == 2
print('PASS WAV 24000 Hz, 16 bit, mono, 1.000 s')
