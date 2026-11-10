"""运行日志：一行一条 JSON，只追加不重写。

为什么不用 `.jobs/*.json` 当日志
--------------------------------
那里每次事件都把**全量**记录重写一遍（含所有历史事件，几十万字符）。
进程被 OOM、断电、任务管理器强杀时，正在写的那份就成了半截文件 ——
丢掉的不是最后一条，而是这次任务的全部记录。

追加日志不一样：崩溃最多丢最后一行（读到坏行就跳过），前面的都在。
续写要回答的第一个问题就是「上次崩在哪一步」——靠的是这条完整序列。

文件位置
--------
`<项目目录>/logs/journal.jsonl`

一行长这样（紧凑单行，方便 `tail` / `grep`）：
    {"seq":42,"at":"2026-11-09T06:12:33+00:00","job":"j_ab...","agent":"writer",
     "phase":"chapter_done","chapter":"c07","nodes":340,"edges":339,
     "digest":"9f2c...","note":"第七章落盘"}
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# 尾部回读的窗口大小：一次读这么多字节，够放几百条记录。
TAIL_WINDOW = 64 * 1024
# 单个字段最长保留多少字符（reason / note 这类人写的文本可能很长）。
FIELD_LIMIT = 400


def graph_digest(graph):
    """图内容的指纹。

    键名排序 + 紧凑分隔符，所以「缩进变了」「键的顺序变了」都不算变化，
    只有**内容真的变了**才换指纹。这正是不该把格式化差异误判成脏数据的理由。
    """
    text = json.dumps(graph, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def now():
    """当前 UTC 时间戳（ISO 格式）。"""
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _short(value, limit=FIELD_LIMIT):
    """把字段裁到可读长度；非字符串原样返回。"""
    if not isinstance(value, str):
        return value
    return value if len(value) <= limit else value[:limit] + '…'


class Journal:
    """一个项目的追加式运行日志。"""

    def __init__(self, root, name='journal.jsonl'):
        """绑定到项目目录下的 logs/。"""
        self.path = Path(root) / 'logs' / name
        self._seq = None

    # ---------- 写 ----------

    def next_seq(self):
        """下一个序号。进程重启后接着上次的序号往下数，不从头开始。

        读不到上次的序号（文件不存在 / 最后一行坏了）就从 0 开始：
        序号只用于排序和「落后多少条」，重复也不要紧。
        """
        if self._seq is None:
            last = self.last()
            self._seq = int(last.get('seq') or 0) if last else 0
        self._seq += 1
        return self._seq

    def append(self, **fields):
        """追加一条记录，顺手 fsync —— 崩溃时这条已经在盘上。"""
        record = dict(fields)
        record.setdefault('seq', self.next_seq())
        record.setdefault('at', now())
        record = {key: _short(value) for key, value in record.items()}
        line = json.dumps(record, ensure_ascii=False, separators=(',', ':'),
                          allow_nan=False) + '\n'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())          # 断电也不丢这一条
        return record

    # ---------- 读 ----------

    def tail(self, limit=20):
        """读最后 limit 条。坏行（崩溃时写了半截的）直接跳过。"""
        if not self.path.is_file():
            return []
        try:
            size = self.path.stat().st_size
            with self.path.open('rb') as stream:
                stream.seek(max(0, size - TAIL_WINDOW))
                chunk = stream.read()
        except OSError:
            return []
        lines = chunk.decode('utf-8', errors='replace').splitlines()
        # 从窗口中间切进来时第一行可能不完整，丢掉
        if size > TAIL_WINDOW and lines:
            lines = lines[1:]
        records = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue                        # 半截行：正常，跳过
            if isinstance(item, dict):
                records.append(item)
        return records[-limit:]

    def last(self):
        """最后一条记录；没有就 None。"""
        records = self.tail(1)
        return records[0] if records else None

    def last_checkpoint(self):
        """最后一条带 digest 的记录（也就是最近一次落盘快照）。"""
        for item in reversed(self.tail(200)):
            if item.get('digest'):
                return item
        return None

    def since(self, seq):
        """取序号大于 seq 的记录（判断「检查点之后又动了什么」用）。"""
        return [item for item in self.tail(500) if int(item.get('seq') or 0) > int(seq)]


def read_log(path, limit=200):
    """读任意一个 JSONL 日志文件的末尾几条（给接口和调试用）。"""
    return Journal(Path(path).parent.parent, name=Path(path).name).tail(limit)


def elapsed_hint(started):
    """把「多久没动」翻成人话，给续写提示用。"""
    gap = max(0.0, time.time() - started)
    if gap < 90:
        return f'{int(gap)} 秒'
    if gap < 5400:
        return f'{int(gap // 60)} 分钟'
    return f'{gap / 3600:.1f} 小时'
