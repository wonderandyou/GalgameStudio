"""续写检查点：图快照 + 完整性校验 + 坏图回滚。

要解决的问题
------------
生成一部作品要跑几十分钟到几小时，中途崩掉是常态。崩了之后必须能回答：

1. **上次写到哪了** —— 哪个 agent、哪一章、多少节点（靠 journal）
2. **盘上那份图是真的还是坏的** —— JSON 能解析吗？内容对得上检查点吗？
3. **如果坏了，能不能回到最近一次好的状态** —— 靠快照

三样东西分工
------------
- `graph.json`      当前真相。原子写保证「要么全新要么全旧」，但**没有内容校验**
- `checkpoints/ck-<seq>.json`  每一个关键时刻的完整快照（滚动保留最近几份）
- `logs/journal.jsonl`  上面两件事的流水账（见 journal.py）

校验口径
--------
快照时记录图的 sha256（键序无关）。续写前重新算一遍比对：

| 情况 | 判定 | 该怎么办 |
|---|---|---|
| 一致 | `clean` | 干净，直接按章节状态续 |
| 节点/边变多 | `advanced` | 正常推进，只是检查点之后又写了新的 |
| 节点/边变少 | `regressed` | 可疑：可能有东西被删/被覆盖，让 agent 看证据判 |
| 读都读不出来 | `corrupt` | 回滚到最近一份能解析的快照 |

「让 agent 判」的意思是：这里只出事实（expected / actual / 差了几个），
不替它下结论。判定和「从哪章续」由 writer agent 自己决定。
"""
import json
from pathlib import Path

from ..store import atomic_json, read_json
from .graph import edges_from_nodes
from .journal import Journal, graph_digest, now


def _edge_count(graph):
    """边数只按 choices 投影计 —— 磁盘上的 edges 可能是旧的冗余副本（D1）。"""
    return len(edges_from_nodes(graph.get('nodes') or []))

# 滚动保留几份快照。500 节点的图约 1~2 MB，保留 5 份也就 10 MB 量级。
SNAPSHOT_KEEP = 5
# 扫描目录时最多看多少个快照文件（防有人手工塞了几万个）。
SCAN_LIMIT = 200


class Checkpoints:
    """一个项目的检查点目录：写快照、校验、回滚。"""

    def __init__(self, root):
        """绑定到 <项目目录>/checkpoints/。"""
        self.root = Path(root)
        self.dir = self.root / 'checkpoints'
        self.journal = Journal(root)

    # ---------- 路径与序号 ----------

    def path_for(self, seq):
        """第 seq 个快照的路径。"""
        return self.dir / ('ck-%06d.json' % seq)

    def snapshots(self):
        """按序号从旧到新列出快照：[{seq, path, meta}]。

        只读每个文件的 meta 字段（不信文件名，信文件里写的序号）。
        解析不了的文件（写一半就崩了）直接跳过 —— 它们本来就不该被用。
        """
        if not self.dir.is_dir():
            return []
        found = []
        for path in sorted(self.dir.glob('ck-*.json'))[:SCAN_LIMIT]:
            try:
                payload = read_json(path)
            except Exception:                       # noqa: BLE001
                continue
            meta = payload.get('meta') if isinstance(payload, dict) else None
            if isinstance(meta, dict) and meta.get('digest'):
                found.append(dict(meta, path=path))
        found.sort(key=lambda item: int(item.get('seq') or 0))
        return found

    def latest(self):
        """最近一份快照的 meta（不带 graph 本体）。"""
        found = self.snapshots()
        return found[-1] if found else None

    # ---------- 写 ----------

    def note(self, graph, *, job=None, agent=None, phase=None, chapter=None, note=''):
        """落一份快照 + 记一条日志。返回 meta。

        `phase` 是语义标签，续写判定读它：`chapter_done` / `stage_start` /
        `stage_done` / `build_graph` / `manual`。
        """
        seq = int((self.journal.last() or {}).get('seq') or 0) + 1
        digest = graph_digest(graph)
        nodes = graph.get('nodes') or []
        meta = dict(seq=seq, at=now(), job=job, agent=agent, phase=phase,
                    chapter=chapter, nodes=len(nodes), edges=_edge_count(graph),
                    digest=digest, note=note)
        atomic_json(self.path_for(seq), dict(meta=meta, graph=graph))
        self.journal.append(**meta)
        self._trim(keep=SNAPSHOT_KEEP)
        return meta

    def _trim(self, keep):
        """只留最近 keep 份快照，旧的删掉。

        删失败不算错 —— 多留几份快照只占点磁盘，不影响正确性。
        """
        found = self.snapshots()
        for item in found[:-keep] if keep > 0 else found:
            try:
                Path(item['path']).unlink()
            except OSError:
                pass

    # ---------- 校验 ----------

    def verify(self, graph):
        """把盘上这份图和最近一次检查点比对，返回结论字典。

        只报事实，不下结论 —— 由 agent（或用户）决定怎么处理。
        """
        last = self.journal.last_checkpoint()
        if last is None:
            return dict(status='empty', reason='这个项目还没有任何检查点（第一次跑）')
        digest = graph_digest(graph)
        if digest == last.get('digest'):
            return dict(status='clean', seq=last.get('seq'), digest=digest,
                        nodes=len(graph.get('nodes') or []),
                        edges=_edge_count(graph),
                        phase=last.get('phase'), chapter=last.get('chapter'),
                        at=last.get('at'))
        nodes = len(graph.get('nodes') or [])
        edges = _edge_count(graph)
        before_nodes = int(last.get('nodes') or 0)
        before_edges = int(last.get('edges') or 0)
        grown = nodes >= before_nodes and edges >= before_edges
        return dict(
            status='advanced' if grown else 'regressed',
            seq=last.get('seq'), phase=last.get('phase'), chapter=last.get('chapter'),
            at=last.get('at'), expected=last.get('digest'), actual=digest,
            nodes=nodes, edges=edges,
            nodes_before=before_nodes, edges_before=before_edges,
            nodes_delta=nodes - before_nodes, edges_delta=edges - before_edges,
            reason=('检查点之后又写了新内容（正常推进）' if grown
                    else '内容比检查点少了 —— 可能有节点/连线被删或图被覆盖过'),
        )

    def verify_file(self, path=None):
        """直接校验磁盘上的 graph.json：能不能解析、指纹是多少。

        给「还没读进内存」的场景用（比如启动时先探一次，别一读就抛）。
        """
        target = Path(path or (self.root / 'graph.json'))
        try:
            graph = read_json(target)
        except Exception as exc:                    # noqa: BLE001
            return dict(status='corrupt', path=str(target),
                        reason=f'{type(exc).__name__}: {exc}', graph=None)
        if not isinstance(graph, dict) or 'nodes' not in graph:
            return dict(status='corrupt', path=str(target),
                        reason='不是合法的图结构（缺 nodes）', graph=None)
        result = self.verify(graph)
        result['path'] = str(target)
        result['graph'] = graph
        return result

    # ---------- 回滚 ----------

    def recover(self):
        """取最近一份能解析的快照，返回 {meta, graph}；没有就 None。

        只负责**给出**内容，写不写回 graph.json 由调用方决定 ——
        生成任务里写回前还要先备份坏图，避免把唯一的现场也覆盖掉。
        """
        for item in reversed(self.snapshots()):
            try:
                payload = read_json(item['path'])
            except Exception:                       # noqa: BLE001
                continue
            graph = payload.get('graph')
            if isinstance(graph, dict) and graph.get('nodes'):
                return dict(meta=item, graph=graph)
        return None

    def summary(self):
        """给接口 / 日志用的一份概览：有多少快照、最近一次是什么状态。"""
        found = self.snapshots()
        return dict(count=len(found), latest=found[-1] if found else None,
                    oldest=found[0] if found else None)
