"""生成阶段的产出、依赖及质量关卡。"""
from dataclasses import dataclass
from functools import cache
from pathlib import PurePosixPath

from ..providers.base import audio_seconds
from .graph import safe_asset, validate_graph


@dataclass(frozen=True)
class StageContract:
    requires: tuple[str, ...]
    produces: tuple[str, ...]
    gate: str


STAGE_CONTRACTS = {
    'writer': StageContract((), ('nodes', 'edges', 'nodes[].lines', 'nodes[].bg.prompt',
                                'nodes[].chars', 'characters', 'agent_state.production_plan'),
                            '唯一首节点、可达、无环、至少一个真结局，剧本和排产表齐全'),
    'art': StageContract(('writer',), ('nodes[].bg.asset', 'nodes[].chars[].pose', 'characters[].ref_image',
                                      'style', 'agent_state.ui（如已生成）'),
                          '每个背景键一张图，立绘和基底存在，占位项明确标记'),
    'voice': StageContract(('writer',), ('nodes[].lines[].voice', 'nodes[].lines[].dur'),
                            '配音文件存在，时长与采样一致'),
    'build': StageContract(('writer', 'art', 'voice'), ('validated_graph',),
                            '图结构和资产通过最终校验，可按所选格式导出'),
    'review': StageContract(('build',), ('review_report',), '汇总各阶段关卡、检查项及降级影响'),
}

MOCK_IMPACTS = {
    'writer': '部分或全部剧本使用内置文案，下游按已提交的剧本和排产表执行',
    'art': '部分或全部图片使用占位素材',
    'voice': '部分或全部台词使用按字数估时的静音音频',
}

OFF_IMPACTS = {
    'voice': '自动配音已关闭，仍显示台词字幕',
    'review': '收尾汇总已关闭，仍保留各阶段的关卡结果',
}


def stage_report(graph):
    """提供可序列化的阶段状态，供完成事件、质检和导出共用。"""
    state = (graph.get('agent_state') or {}).get('pipeline') or {}
    report = {key: dict(state.get(key) or {}) for key in (
        'stages', 'degraded', 'impacts', 'gates', 'dependencies', 'failures')}
    for stage, status in report['stages'].items():
        fallback = {'mock': MOCK_IMPACTS, 'off': OFF_IMPACTS}.get(status, {})
        report['impacts'].setdefault(stage, fallback.get(stage, ''))
    return report


def check_writer(graph):
    validate_graph(graph, auto_voice=False)
    nodes = graph['nodes']
    if nodes[0]['type'] != 'start' or not any(
            node['type'] == 'ending' and node.get('end_kind') == 'true' for node in nodes):
        raise ValueError('编剧产出须从唯一的起点开始，并包含至少一个真结局')
    state = graph['agent_state']
    if state.get('skeleton') is not None and any(node['status'] != 'done' for node in nodes):
        raise ValueError('冻结的骨架仍有节点未填充')
    if any(not line['text'].strip() for node in nodes for line in node['lines']):
        raise ValueError('编剧产出包含空白台词')
    plan = state.get('production_plan')
    if not isinstance(plan, dict) or not plan.get('version'):
        raise ValueError('编剧产出缺少有效的排产表')
    voice = plan.get('voice')
    if (not isinstance(plan.get('backgrounds'), list) or not isinstance(plan.get('poses'), list)
            or not isinstance(voice, dict) or not isinstance(voice.get('order'), list)):
        raise ValueError('编剧排产表缺少背景、立绘或配音批次')
    return dict(nodes=len(nodes), backgrounds=len(plan['backgrounds']),
                poses=len(plan['poses']), plan_source=plan.get('source'))


def check_art(graph, root, backgrounds, is_placeholder):
    """检验渲染实际使用的路径，并区分真图和已落盘的占位。"""
    @cache
    def require_asset(asset):
        if not asset or not safe_asset(root, asset).is_file():
            raise ValueError(f'美术素材不存在：{asset or "空路径"}')

    mock_backgrounds = 0
    for key, nodes in backgrounds.items():
        assets = {node['bg']['asset'] for node in nodes}
        if len(assets) != 1:
            raise ValueError(f'背景键 {key!r} 对应 {len(assets)} 张图')
        asset = next(iter(assets))
        require_asset(asset)
        mock_backgrounds += int(is_placeholder(asset) or any(
            node['bg']['provider'] in ('mock', 'pending') for node in nodes))

    bases = {char['id']: char['ref_image'] for char in graph['characters']}
    poses = set()
    for node in graph['nodes']:
        for pose in node['chars']:
            if pose['id'] not in bases:
                raise ValueError(f'节点 {node["id"]} 的立绘角色 {pose["id"]!r} 不存在')
            poses.add(pose['pose'])
    for asset in sorted(poses | set(bases.values())):
        require_asset(asset)

    if not graph.get('style'):
        raise ValueError('美术阶段缺少风格圣经')
    ui = (graph['agent_state'].get('ui') or {}).values()
    mock_ui = 0
    for surface in ui:
        # 预设和界面设计元数据可能与已生成界面图共存在 ui 状态中。
        if not isinstance(surface, dict) or 'asset' not in surface:
            continue
        require_asset(surface['asset'])
        mock_ui += int(surface.get('provider') == 'mock' or is_placeholder(surface['asset']))
    return dict(backgrounds=len(backgrounds), bases=len(bases), poses=len(poses),
                mock_backgrounds=mock_backgrounds, mock_bases=sum(map(is_placeholder, bases.values())),
                mock_poses=sum(map(is_placeholder, poses)), mock_ui=mock_ui)


def check_voice(graph, root):
    @cache
    def duration(asset):
        path = safe_asset(root, asset)
        seconds = audio_seconds(path.read_bytes()) if path.is_file() else None
        if not seconds or seconds <= 0:
            raise ValueError(f'语音文件缺失或读不出时长：{asset}')
        return seconds

    lines = [line for node in graph['nodes'] for line in node['lines']]
    silent = 0
    for line in lines:
        asset, seconds = line['voice'], line['dur']
        if not asset or not seconds > 0 or abs(duration(asset) - seconds) >= 0.15:
            raise ValueError(f'语音缺失或实际时长不一致：{asset or "空路径"}')
        name = PurePosixPath(asset).name
        silent += int(name == 'mock.wav' or name.startswith('silent_'))
    return dict(lines=len(lines), silent=silent)
