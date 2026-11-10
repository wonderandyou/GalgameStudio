import json
import wave
from pathlib import Path, PurePosixPath
from ..contract import Graph

def safe_asset(root, value):
    path = PurePosixPath(value)
    if '\\' in value or ':' in value or path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'assets':
        raise ValueError(f'非法资产路径: {value}')
    if root is None:
        return Path(value)
    target = (Path(root) / value).resolve()
    if not target.is_relative_to(Path(root).resolve()):
        raise ValueError(f'资产越界: {value}')
    return target

def edges_from_nodes(nodes):
    return [dict(id=f'e_{n["id"]}_{i}', **{'from': n['id']}, port=i, to=c['to'])
            for n in nodes for i, c in enumerate(n.get('choices', []))]

def validate_graph(graph, root=None, auto_voice=True):
    Graph.model_validate(graph)
    nodes = graph['nodes']
    by_id = {n['id']: n for n in nodes}
    if len(by_id) != len(nodes):
        raise ValueError('节点 ID 重复')
    starts = [n for n in nodes if n['type'] == 'start']
    if len(starts) != 1 or starts[0]['id'] != 'n1' or not any(n['type'] == 'ending' for n in nodes):
        raise ValueError('必须有唯一 start n1 和至少一个 ending')
    expected = edges_from_nodes(nodes)
    actual = [(e['from'], e['port'], e['to']) for e in graph['edges']]
    if sorted(actual) != sorted((e['from'], e['port'], e['to']) for e in expected):
        raise ValueError('edges 与 choices 不一致')
    if len({e['id'] for e in graph['edges']}) != len(graph['edges']):
        raise ValueError('边 ID 重复')
    for n in nodes:
        choices = n.get('choices', [])
        if n['type'] == 'ending':
            if 'choices' in n or n.get('end_kind') not in ('true', 'bad', 'alt'):
                raise ValueError('ending 必须有 end_kind 且无 choices')
        elif n['type'] == 'choice':
            if not 2 <= len(choices) <= 4:
                raise ValueError('choice 必须有 2–4 个 choices')
        elif len(choices) != 1:
            raise ValueError('start/scene 必须有一个后继')
        for c in choices:
            if c['to'] not in by_id:
                raise ValueError('choices 指向不存在的节点')
        paths = [n['bg']['asset']] + [c['pose'] for c in n['chars']]
        for line in n['lines']:
            if auto_voice and not line['voice']:
                raise ValueError('缺少 voice')
            if abs(line['dur'] - len(line['text']) / 4.5) / (len(line['text']) / 4.5) >= 0.6:
                raise ValueError('语音时长与字数偏差超过 60%')
            if line['voice']:
                paths.append(line['voice'])
                if root:
                    with wave.open(str(safe_asset(root, line['voice'])), 'rb') as audio:
                        duration = audio.getnframes() / audio.getframerate()
                    if abs(duration - line['dur']) >= 0.15:
                        raise ValueError('语音实际时长不一致')
        for asset in paths:
            target = safe_asset(root, asset)
            if root and not target.is_file():
                raise ValueError(f'缺少资产: {asset}')
    seen, visiting = set(), set()
    def visit(node_id):
        if node_id in visiting:
            raise ValueError('图中存在环')
        if node_id in seen:
            return
        visiting.add(node_id)
        for choice in by_id[node_id].get('choices', []):
            visit(choice['to'])
        visiting.remove(node_id)
        seen.add(node_id)
    visit('n1')
    if seen != set(by_id):
        raise ValueError('存在不可达/孤立节点')
    for character in graph['characters']:
        target = safe_asset(root, character['ref_image'])
        if root and not target.is_file():
            raise ValueError('角色参考图缺失')
    return graph

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', type=Path, required=True)
    parser.add_argument('--no-voice', action='store_true')
    args = parser.parse_args()
    validate_graph(json.loads(args.check.read_text('utf-8')), args.check.parent, not args.no_voice)
    print('GRAPH_VALID')
