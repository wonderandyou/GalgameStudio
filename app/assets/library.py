"""素材库：跨项目复用 + 分层派生 + 预算闸门。

为什么要有这一层
----------------
一部上千节点的 galgame，如果每个节点都按自己的台词去生图，就是上千张图、
上千次调用、几个小时。但真实需求里：

* 背景：同一地点在同一时间/天气下，**几十个节点共用一张**就够了；
* 立绘：一个角色一共几个表情、几个姿势，**不是每个节点一个表情**。

所以素材库按「结构化的 key」索引，而不是按节点 id：

    L0 角色基底  char:<id>:base                  一个角色一张，txt2img 只跑一次
    L1 表情姿势  char:<id>:<pose>:<mood>         从 L0 派生（有编辑模型时只改局部）
    L2 背景      bg:<地点>|<时间>|<天气>          同一四元组只存一张

图片按内容寻址落盘（files/<sha 前两位>/<sha>.png）：
两个 key 指向同一张图时磁盘上只有一份，跨项目也共用。

这一层不认识 graph 结构，只认 key 和字节 —— 方便单测，也方便前端直接查库。
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import struct

# 姿势与表情用**枚举**而不是自由文本。
# 自由文本会让「微笑」「微微一笑」「笑」变成三张图；枚举则天然可复用，
# 也方便前端做表情切换面板。
POSE = ('stand', 'sit', 'lean', 'lie', 'walk')
MOOD = ('neutral', 'smile', 'sad', 'angry', 'surprise', 'blush', 'cry')

# 时间与天气是**区分维度**，不能归一化掉：
# 「傍晚的天台」和「深夜的天台」必须是两张图。
TIMES = ('dawn', 'day', 'dusk', 'night')
WEATHERS = ('clear', 'cloudy', 'rain', 'snow', 'fog')

# 预算闸门：不是硬上限，是「规划阶段拿来算账」的数字。
# 千节点项目只要这几个数字，实际生图量就压到百张以内。
BUDGET = dict(bg=40, char=8, pose_per_char=12)

_ALIAS = {
    '早晨': 'dawn', '清晨': 'dawn', '黎明': 'dawn', '早上': 'dawn',
    '白天': 'day', '正午': 'day', '中午': 'day', '午后': 'day', '下午': 'day',
    '傍晚': 'dusk', '黄昏': 'dusk', '日落': 'dusk', '夕': 'dusk', '夕阳': 'dusk', '晚霞': 'dusk',
    '夜': 'night', '深夜': 'night', '晚上': 'night', '夜里': 'night', '凌晨': 'night',
    '晴': 'clear', '晴天': 'clear',
    '阴': 'cloudy', '阴天': 'cloudy', '多云': 'cloudy',
    '雨': 'rain', '雨天': 'rain', '下雨': 'rain', '小雨': 'rain', '大雨': 'rain',
    '雪': 'snow', '雪天': 'snow', '下雪': 'snow',
    '雾': 'fog', '雾天': 'fog', '起雾': 'fog',
}

_PUNCT = re.compile(r'[\s\u3000·・,，.。;；:：!！?？()（）\[\]【】"“”\'‘’/\\|-]+')
_TOKEN = re.compile(r'[\u4e00-\u9fff]|[a-z0-9]+')


def normalize(text):
    """归一化：全角转半角、去标点空白、小写。用于 key 与相似度。"""
    if not text:
        return ''
    value = str(text).strip().lower().translate(str.maketrans('ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ０１２３４５６７８９',
                                                             'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'))
    return _PUNCT.sub('', value)


def tokens(text):
    """切成「单字 + 英文词」的集合。中文按单字，避免分词依赖。"""
    return _TOKEN.findall(normalize(text))


def similarity(a, b):
    """两段描述的字面相似度。

    只用 Jaccard 会误杀短描述：「学校天台」vs「天台」= 2/4 = 0.5，
    但后者完整包含在前者里，几乎肯定是同一个地方。
    所以取 (Jaccard + 包含度) 的均值 —— 包含度 = 交集 / 较短的一方。

    它只回答「是不是同一个地点」，不代替语义理解：
    所以最终判定还要叠加时间/天气**必须完全相同**。
    """
    left, right = set(tokens(a)), set(tokens(b))
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    jaccard = overlap / len(left | right)
    containment = overlap / min(len(left), len(right))
    return 0.5 * jaccard + 0.5 * containment


def _pick(text, table, default):
    """从一段中文/英文描述里认出归类维度。认不出就返回 default。"""
    value = normalize(text)
    for word, canonical in table.items():
        if normalize(word) in value:
            return canonical
    return default


def classify_time(text):
    """从场景描述里猜时段（白天 / 黄昏 / 夜），生图提示词要用。"""
    return _pick(text, {k: v for k, v in _ALIAS.items() if v in TIMES}, 'day')


def classify_weather(text):
    """从场景描述里猜天气（晴 / 雨 / 雪）。"""
    return _pick(text, {k: v for k, v in _ALIAS.items() if v in WEATHERS}, 'clear')


_FILLER = ('的', '之', '里', '内', '中', '上', '下', '边', '处', '旁')


def _strip(text, words):
    """把「黄昏」「小雨」「的」这类修饰词从地点描述里抠掉，只留地点本身。

    不抠的话，「黄昏的天台」和「天台的黄昏」会是两个 key，各生一张图。
    长的先删，否则「黄昏」会被拆成碎字。
    """
    value = normalize(text)
    for word in sorted(words, key=len, reverse=True):
        value = value.replace(normalize(word), '')
    for word in _FILLER:
        value = value.replace(word, '')
    return value


def _place_name(scene):
    """从一句场景描述里提取纯地点名。"""
    return _strip(scene, list(_ALIAS)) or normalize(scene) or 'unknown'


def bg_key(scene, time='', weather='', with_time=True):
    """背景 key：地点 + 时间 + 天气。

    `with_time=False` 用于「这个地点全天通用」的场景（如室内、走廊），
    这样同一地点的白天和夜晚会自动合并成一张，进一步省图。
    """
    place = _place_name(scene)
    if not with_time:
        return f'bg:{place}'
    # 存 key 和查库必须用同一套 canonical 值，否则「黄昏」和「dusk」永远对不上。
    moment = classify_time(time or scene)
    sky = classify_weather(weather or scene)
    return f'bg:{place}|{moment}|{sky}'


def base_key(char_id):
    """角色基底立绘的 key。一个角色只有一张。"""
    return f'char:{_safe_id(char_id)}:base'


def pose_key(char_id, pose, mood):
    """表情姿势差分的 key。

    pose/mood 不在枚举内的会落到最近的可选项，而不是原样拼进 key ——
    否则模型写「微微一笑」就会多出一张图。
    """
    return f'char:{_safe_id(char_id)}:{_pose(pose)}:{_mood(mood)}'


def _safe_id(value):
    """把任意字符串变成合法的资源 id（只留字母数字下划线）。"""
    return re.sub(r'[^a-z0-9_]', '', str(value or '').lower()) or 'c'


_POSE_WORDS = (
    ('坐', 'sit'), ('倚', 'lean'), ('靠', 'lean'), ('躺', 'lie'), ('卧', 'lie'),
    ('走', 'walk'), ('行', 'walk'), ('站', 'stand'), ('立', 'stand'),
)


def _pose(value):
    """取立绘对应的文件路径。"""
    text = normalize(value)
    for item in POSE:
        if item == text or item in text:
            return item
    for word, pose in _POSE_WORDS:
        if normalize(word) in text:
            return pose
    return 'stand'


def _mood(value):
    """取表情标签（默认「平静」）。"""
    text = normalize(value)
    for item in MOOD:
        if item == text or item in text:
            return item
    # 中文情绪先做一次映射，认不出才退回 neutral
    for word, mood in (('笑', 'smile'), ('高兴', 'smile'), ('开心', 'smile'),
                       ('哭', 'cry'), ('泪', 'cry'), ('悲', 'sad'), ('难', 'sad'), ('低落', 'sad'),
                       ('怒', 'angry'), ('生气', 'angry'), ('愤', 'angry'),
                       ('惊', 'surprise'), ('讶', 'surprise'), ('意外', 'surprise'),
                       ('羞', 'blush'), ('脸红', 'blush')):
        if normalize(word) in text:
            return mood
    return 'neutral'


def stance_of(value):
    """把「坐着 / 靠墙 / 走过」这类描述归一成 POSE 枚举之一（默认 stand）。

    `_pose` 的公开入口：立绘要按「姿态 + 表情」做差分，写入端和取 key 端必须用
    同一套归一化，否则「坐」和「sit」会被当成两种姿态、画出两张一样的图。
    """
    return _pose(value)


def mood_of(value):
    """把「微微一笑 / 哭 / 平静」这类描述归一成 MOOD 枚举之一（默认 neutral）。"""
    return _mood(value)


def key_kind(key):
    """判断一个素材键属于哪一类（背景 / 立绘 / 语音 / 参考图）。"""
    if key.startswith('bg:'):
        return 'bg'
    if key.endswith(':base'):
        return 'char_base'
    return 'char_pose'


def image_size(data):
    """读 PNG / JPEG 的像素尺寸；读不出返回 None。纯标准库，不引入 Pillow。"""
    if data[:8] == b'\x89PNG\r\n\x1a\n' and len(data) >= 24:
        return struct.unpack('>II', data[16:24])
    if data[:2] == b'\xff\xd8':
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                height, width = struct.unpack('>HH', data[index + 5:index + 9])
                return width, height
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            length = struct.unpack('>H', data[index + 2:index + 4])[0]
            index += 2 + length
    return None


class Library:
    """素材库索引。`root` 是库目录，索引写在 `root/index.json`。

    读取是懒加载 + 进程内缓存；写入走「写临时文件再替换」，
    避免生图中途崩掉留下半个 JSON（以前的 credentials 就踩过这个坑）。
    """

    def __init__(self, root):
        self.root = Path(root)
        self.index_path = self.root / 'index.json'
        self._entries = None

    # ---- 索引读写 ----
    @property
    def entries(self):
        if self._entries is None:
            self._entries = self._load()
        return self._entries

    def _load(self):
        try:
            raw = json.loads(self.index_path.read_text('utf-8'))
        except (OSError, ValueError):
            return {}
        items = raw.get('entries') if isinstance(raw, dict) else None
        return items if isinstance(items, dict) else {}

    def save(self):
        """原子落盘。库可能被多个项目共用，写入失败不能让调用方拿到半份数据。"""
        self.root.mkdir(parents=True, exist_ok=True)
        payload = dict(version=1, updated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                       entries=dict(sorted(self.entries.items())))
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
        temp = self.index_path.with_suffix('.json.tmp')
        temp.write_bytes(data)
        # Windows 上只读句柄调 fsync 会 Bad file descriptor，必须用 r+b 打开。
        with open(temp, 'r+b') as handle:
            os.fsync(handle.fileno())
        os.replace(temp, self.index_path)

    # ---- 查询 ----
    def get(self, key):
        """按 id 取一个预设；不存在返回 None。"""
        return self.entries.get(key)

    def find_bg(self, scene, time='', weather='', threshold=0.72):
        """按地点找已有背景，返回 `(key, entry, how)`；没有则 `(None, None, '')`。

        先按精确 key 找；找不到再按令牌相似度找**同时间同天气**的近似地点，
        这样「学校天台」和「天台」不会各生一张。
        返回命中的 key 是为了让调用方 bump 计数 —— 命中的可能是相似项的 key。
        """
        key = bg_key(scene, time, weather)
        exact = self.get(key)
        if exact:
            return key, exact, 'exact'
        moment = classify_time(time or scene)
        sky = classify_weather(weather or scene)
        best, score = None, 0.0
        for candidate_key, entry in self.entries.items():
            if key_kind(candidate_key) != 'bg' or entry.get('missing'):
                continue
            parts = candidate_key[3:].split('|')
            if len(parts) == 3 and (parts[1] != moment or parts[2] != sky):
                continue  # 时间或天气不同，不许复用
            candidate = similarity(scene, parts[0])
            if candidate > score:
                best, score = candidate_key, candidate
        if best and score >= threshold:
            return best, self.entries[best], f'similar:{score:.2f}'
        return None, None, ''

    def find_pose(self, char_id, pose, mood):
        """按角色+姿势+表情找差分；没有就退回该角色的基底。"""
        key = pose_key(char_id, pose, mood)
        entry = self.get(key)
        if entry:
            return key, entry, 'exact'
        base = base_key(char_id)
        entry = self.get(base)
        if entry:
            return base, entry, 'base_fallback'
        return None, None, ''

    # ---- 写入 ----
    def put(self, key, data, **meta):
        """存一张图（或一段音频）。内容寻址：同一份字节只落一次盘。"""
        digest = sha256(data).hexdigest()
        relative = f'files/{digest[:2]}/{digest}{_suffix(data)}'
        target = self.root / relative
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(target.suffix + '.tmp')
            temp.write_bytes(data)
            os.replace(temp, target)
        entry = dict(kind=key_kind(key), file=relative, sha256=digest,
                     bytes=len(data), created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                     uses=0, **meta)
        size = image_size(data)
        if size:
            entry['size'] = list(size)
        self.entries[key] = entry
        self.save()
        return entry

    def bump(self, key, count=1):
        """记一次复用。复用次数是「这张图值不值得留下」的依据。"""
        entry = self.entries.get(key)
        if entry is None:
            return None
        entry['uses'] = int(entry.get('uses', 0)) + count
        entry['last_used'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        return entry

    # ---- 预算 ----
    def stats(self):
        groups = {'bg': 0, 'char_base': 0, 'char_pose': 0}
        for key, entry in self.entries.items():
            if entry.get('missing'):
                continue
            kind = key_kind(key)
            groups[kind] = groups.get(kind, 0) + 1
        return groups

    def within_budget(self, budget=None, extra=()):
        """还能不能再生成。用于「千节点不烧钱」的硬闸门。

        `extra` 是本次计划新增的 key，调用方先算账再动手。
        """
        limits = dict(BUDGET, **(budget or {}))
        counts = self.stats()
        # 已经在库里的 key 不算新增，否则「重复引用同一张图」会被误判超预算。
        extra = [key for key in extra if key not in self.entries]
        for key in extra:
            kind = key_kind(key)
            counts[kind] = counts.get(kind, 0) + 1
        if counts['bg'] > limits['bg']:
            return False, f'背景素材已达上限 {limits["bg"]} 张'
        if counts['char_base'] > limits['char']:
            return False, f'角色数已达上限 {limits["char"]} 个'
        poses = {}
        for key in list(self.entries) + list(extra):
            if key_kind(key) == 'char_pose':
                poses[key.split(':')[1]] = poses.get(key.split(':')[1], 0) + 1
        for char_id, count in poses.items():
            if count > limits['pose_per_char']:
                return False, f'角色 {char_id} 的差分已达上限 {limits["pose_per_char"]} 张'
        return True, ''


def _suffix(data):
    """给文件名加去重后缀。"""
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    if data[:2] == b'\xff\xd8':
        return '.jpg'
    if data[:4] == b'RIFF':
        return '.wav'
    return '.bin'
