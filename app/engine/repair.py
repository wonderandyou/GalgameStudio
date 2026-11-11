"""按 `agent_state.skeleton` 的原始拓扑修复被旧前端保存弄坏的图（commit D）。

来源：主人写的 `tools/repair_from_skeleton.py`。核心逻辑搬到这里，让
命令行工具、checkpoint 恢复、启动自检、API 共用同一份实现。

修复规则（和工具原版一致，外加两处收紧）：
- `agent_state.nodes[nid].ref` → skeleton 节点；用 skeleton 的 `to` 重建 `choices[].to`
- label：按 port 下标沿用旧值；旧值为空时用 skeleton 的 labels；再没有才给默认值
- 收紧 1：旧出口的 condition / effects 只在**旧出口目标与新目标相同**时沿用，
  否则用 skeleton 的 effects —— 防止把别的出口的旗标挂到新目标上
- 收紧 2：kind 一律按目标节点重算（目标是 ending 用它的 end_kind，否则 chapter），
  和 skeleton.to_graph_stub 同一规则
- edges 由 choices 重算（normalize_graph）

本模块的纯函数**不写盘**。写盘只有 `repair_project(..., apply=True)` 一条路：
先过 apply_graph 严格闸，通过后再备份原文件，最后原子写。
"""
import copy
import logging
import shutil
from datetime import datetime
from pathlib import Path

from ..store import atomic_json, read_json
from .graph import apply_graph, normalize_graph, structure_problems

log = logging.getLogger('gal.repair')

DEFAULT_LABELS = {'继续'} | {'选项 ' + chr(65 + i) for i in range(6)}


def skeleton_nodes(graph):
    """skeleton 节点列表；没有骨架返回 []。"""
    return list(((graph.get('agent_state') or {}).get('skeleton') or {}).get('nodes') or [])


def has_skeleton(graph):
    """能不能按骨架修：要有 skeleton.nodes，也要有 agent_state.nodes 的 nid→ref 映射。"""
    return bool(skeleton_nodes(graph)) and bool((graph.get('agent_state') or {}).get('nodes'))


def dead_ends(graph):
    """非 ending 却没有任何出口的节点 id（玩家走到这里就卡死）。"""
    return [n['id'] for n in graph.get('nodes') or []
            if n.get('type') != 'ending' and not (n.get('choices') or [])]


def visible_texts(graph):
    """choice 节点上玩家看得见的 (节点, 选项文字) 集合（去重；「继续 / 选项 A…」这类兜底占位不算）。"""
    texts = {(n['id'], (c.get('label') or '').strip())
             for n in graph.get('nodes') or [] if n.get('type') == 'choice'
             for c in n.get('choices') or []}
    return {item for item in texts if item[1] and item[1] not in DEFAULT_LABELS}


def rebuild(graph):
    """返回 (新图, 统计)；不改入参。不能修（没有骨架/映射）时返回 (None, {reason})。"""
    if not has_skeleton(graph):
        return None, dict(reason='没有 skeleton 或没有 agent_state.nodes')
    doc = copy.deepcopy(graph)
    state = doc['agent_state']
    sk_by_ref = {n['ref']: n for n in skeleton_nodes(doc) if isinstance(n, dict) and n.get('ref')}
    ref_of = {nid: m.get('ref') for nid, m in state['nodes'].items() if isinstance(m, dict)}
    ref_to_id = {}
    duplicate_refs = []
    for nid, ref in ref_of.items():
        if not ref:
            continue
        if ref in ref_to_id:
            duplicate_refs.append(ref)
        ref_to_id[ref] = nid
    if duplicate_refs:
        return None, dict(reason='agent_state.nodes 里有重复 ref：%s' % '、'.join(sorted(set(duplicate_refs))))
    by_id = {n['id']: n for n in doc['nodes']}

    kept = from_skeleton = native = 0
    skipped = []
    for node in doc['nodes']:
        sk = sk_by_ref.get(ref_of.get(node['id']) or '')
        if sk is None:
            skipped.append(node['id'])
            continue
        targets = [ref_to_id.get(ref) for ref in sk.get('to') or []]
        if any(t is None or t not in by_id for t in targets):
            skipped.append('%s(丢%d个出口)' % (node['id'], sum(t is None or t not in by_id for t in targets)))
            continue
        old = node.get('choices') or []
        sk_labels = sk.get('labels') or []
        sk_effects = sk.get('effects') or []
        new = []
        for i, to in enumerate(targets):
            prev = old[i] if i < len(old) else {}
            if (prev.get('label') or '').strip():
                label = prev['label']
                kept += 1
            elif i < len(sk_labels) and isinstance(sk_labels[i], str) and sk_labels[i].strip():
                label = sk_labels[i]
                from_skeleton += 1
            else:
                label = '继续' if len(targets) == 1 else '选项 ' + chr(65 + i)
                native += 1
            target = by_id[to]
            kind = target.get('end_kind') if target.get('type') == 'ending' else 'chapter'
            choice = dict(label=label, to=to, condition=None, kind=kind)
            if prev.get('to') == to:
                choice['condition'] = prev.get('condition')
                if prev.get('effects'):
                    choice['effects'] = copy.deepcopy(prev['effects'])
            elif i < len(sk_effects) and sk_effects[i]:
                choice['effects'] = {sk_effects[i]: True}
            new.append(choice)
        if new:
            node['choices'] = new
        else:
            node.pop('choices', None)
    out = normalize_graph(doc)
    lost = sorted(visible_texts(graph) - visible_texts(out))
    return out, dict(kept=kept, from_skeleton=from_skeleton, native=native, skipped=skipped,
                     lost_visible=lost)


def report(graph):
    """只读诊断：修复前后的问题数 / 断头数 / 可见文字丢失。不写盘。"""
    before = structure_problems(normalize_graph(graph))
    result = dict(has_skeleton=has_skeleton(graph), problems_before=len(before),
                  dead_ends_before=len(dead_ends(graph)), problems=before)
    if not result['has_skeleton']:
        result['repairable'] = False
        # ★ 必须给出文字原因：只有 repairable=false 一个布尔，用户和前端都只能猜
        #   「为什么不能修」。三种情况的理由完全不同，要分开说（has_skeleton 要求
        #   骨架节点和 agent_state.nodes 映射**都**在，缺哪一样修法就不一样）。
        atoms = skeleton_nodes(graph)
        refs = (graph.get('agent_state') or {}).get('nodes')
        if not atoms and not refs:
            why = ('这张图既没有 agent_state.skeleton.nodes（骨架拓扑），'
                   '也没有 agent_state.nodes（节点→骨架的映射）')
        elif not atoms:
            why = '这张图没有 agent_state.skeleton.nodes（新建项目 / 手工编辑 / 旧版分章产物都没有）'
        else:
            why = '这张图有骨架，但缺少 agent_state.nodes（记录每个节点对应骨架哪一步的映射）'
        result['reason'] = why + '；没有骨架可参照，无法按骨架重建出口'
        return result, None
    if not before:
        result['repairable'] = False
        result['reason'] = '这张图的结构没有问题（I1–I8 全部通过），不需要修复'
        return result, None
    built, stat = rebuild(graph)
    if built is None:
        result.update(repairable=False, reason=stat['reason'])
        return result, None
    after = structure_problems(built)
    result.update(stat, problems_after=len(after), problems_left=after,
                  dead_ends_after=len(dead_ends(built)), repairable=not after)
    return result, built


def repair_hint(graph):
    """启动自检 / API 用：图不合法且有骨架 → 返回提示 dict；否则 None。**不修、不写。**"""
    if not has_skeleton(graph):
        return None
    problems = structure_problems(normalize_graph(graph))
    if not problems:
        return None
    info, _ = report(graph)
    return dict(problems=len(problems), dead_ends=info['dead_ends_before'],
                repairable=info['repairable'], problems_after=info.get('problems_after'),
                lost_visible=len(info.get('lost_visible') or []),
                hint='可以按骨架修复（默认只预览；确认后显式 apply 才写盘，写前自动备份）')


def backup_graph(path, backup_root, project_id, stamp=None):
    """把当前 graph.json 原样复制到 `<backup_root>/<时间戳>/<项目id>_graph.json`。"""
    stamp = stamp or datetime.now().strftime('%Y%m%d-%H%M%S')
    folder = Path(backup_root) / stamp
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ('%s_graph.json' % project_id)
    shutil.copy2(path, target)
    return target


def repair_project(root, *, backup_root, apply=False, source='repair.skeleton', stamp=None):
    """修一个项目目录。默认 dry-run（只返回报告）。

    apply=True 时：严格闸 apply_graph 先过（不过直接抛 GraphRejected，什么都不写）→
    备份原 graph.json → atomic_json 写回。返回报告（含 backup 路径）。
    """
    root = Path(root)
    path = root / 'graph.json'
    graph = read_json(path)
    info, built = report(graph)
    info['project_id'] = root.name
    info['applied'] = False
    if not apply or built is None or not info['repairable']:
        return info
    checked = apply_graph(built, source=source)      # 有 skeleton → 严格闸，有问题就整体拒绝
    info['backup'] = str(backup_graph(path, backup_root, root.name, stamp))
    atomic_json(path, checked)
    info['applied'] = True
    log.warning('按骨架修复已写盘 project=%s problems %d→0 dead_ends %d→%d backup=%s',
                root.name, info['problems_before'], info['dead_ends_before'],
                info['dead_ends_after'], info['backup'])
    return info


def scan_hints(projects_root):
    """启动自检：扫一遍项目目录，返回 {项目id: hint}。只读。"""
    hints = {}
    for path in sorted(Path(projects_root).glob('*/graph.json')):
        try:
            graph = read_json(path)
        except Exception:                                   # noqa: BLE001
            continue
        if not isinstance(graph, dict) or not graph.get('nodes'):
            continue
        try:
            hint = repair_hint(graph)
        except Exception as exc:                            # noqa: BLE001
            log.warning('启动自检跳过 %s：%s: %s', path.parent.name, type(exc).__name__, exc)
            continue
        if hint is not None:
            hints[path.parent.name] = hint
            log.warning('项目 %s 的图有 %d 项结构问题（%d 个断头），且有骨架：%s；'
                        '预览 GET /api/projects/%s/repair-from-skeleton，确认后 POST 同一地址 {"apply": true}',
                        path.parent.name, hint['problems'], hint['dead_ends'], hint['hint'], path.parent.name)
    return hints
