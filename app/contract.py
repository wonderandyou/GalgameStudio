"""graph.json 的公开数据契约；mock 与后续真实生成共用。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_serializer

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Project(Model):
    id: str
    name: str
    resolution: tuple[int, int]
    created_at: str
    # 导出成品的展示信息（和剧情无关，只影响打包出来的东西）：
    #   game_title —— 游戏里显示的名字，空则回落到 name
    #   icon       —— 图标，存成 data:image/...;base64,... 的小图（≤256KB），
    #                 导出时解码写进成品，不用单独管理上传文件的生命周期
    game_title: str = ''
    icon: str = ''

class Style(Model):
    """风格圣经：一部作品里所有视觉资产的唯一来源。

    生图 agent 每出一张图之前都必须先读它，不许各自为政 ——
    否则同一个项目里的立绘、背景、界面会变成三套画风。
    """
    palette: list[str] = []      # 色板：主色 / 辅色 / 强调色 / 中性色（可带 # 的十六进制）
    medium: str = ''             # 画法：赛璐璐 / 厚涂 / 水墨 / 描线
    light: str = ''              # 光影基调：色温、对比、主光方向
    typeface: str = ''           # 字体方案：标题 / 正文 / UI
    negative: list[str] = []     # 全局负面词，每张图都带
    refs: list[str] = []         # 参考图（素材库路径），只给生图模型看
    version: int = 1

class Character(Model):
    id: str
    name: str
    color: str
    ref_image: str
    voice_id: str
    # 外形锚点：逐字复用到每一次立绘提示词里。
    # 不写的话，第 30 章她会变成另一个人 —— 这是立绘崩坏的头号原因。
    look: str = ''
    # 性别：由参考图上传时指定，agent 写台词时要靠它分「他/她」。
    gender: Literal['male', 'female', 'other'] = 'other'

class Background(Model):
    asset: str
    prompt: str
    seed: int
    provider: str
    # 结构化字段：决定「是不是同一个地方」。
    # prompt 是给生图模型看的散文，这三项是给素材库算账用的，
    # 混在一起就会把「黄昏的天台」和「天台的黄昏」判成两张图。
    place: str = ''
    time: str = ''
    weather: str = ''

class Pose(Model):
    id: str
    pose: str
    pos: Literal['left', 'center', 'right']
    mood: str
    # 姿势枚举（stand/sit/lean/lie/walk）。`pose` 历史上是图片路径，
    # 这里单独放「姿态」，否则「坐着」和「站着」永远共用同一张图。
    stance: str = 'stand'

class Line(Model):
    who: str
    text: str = Field(min_length=1)
    voice: str | None
    dur: float = Field(gt=0, allow_inf_nan=False)

class Choice(Model):
    label: str
    to: str
    # 显示条件：`flag`（旗标为真才显示）/ `!flag`（旗标为假才显示），None = 总是显示。
    condition: str | None = None
    # 选中后写入的旗标：{旗标名: 值}。cosmetic（flavor）选项靠它留下「选过什么」。
    effects: dict[str, bool | int] | None = None
    # 这个选项通向哪里：'chapter' 进下一章，'true'/'good'/'bad'/'alt' 切对应结局。
    # 可选（旧项目没有这个字段），由骨架校验保证它和目标节点一致。
    kind: str | None = None

class Position(Model):
    x: float
    y: float

class Generation(Model):
    model: str
    at: str
    cost: float = Field(ge=0)

class NodeReview(Model):
    """逐节点审核的结论（用户人工过审时写入）。"""
    status: Literal['pass', 'fail']
    note: str = ''
    at: str = ''

class Node(Model):
    id: str
    type: Literal['start', 'scene', 'choice', 'ending']
    title: str
    status: Literal['pending', 'generating', 'done', 'failed']
    # 结局分四类（主人 2026-11-08 定）：
    #   true 真结局  —— 要特定选择组合才能到，信息量最大
    #   good 好结局  —— 两全的走法，玩家第一次通关通常会到这里
    #   bad  坏结局  —— 不是「死掉」，是某种关系的不可挽回
    #   alt  分歧结局 —— 另一种合理的生活，谈不上好坏
    end_kind: Literal['true', 'good', 'bad', 'alt'] | None = None
    # 分支分两类（主人 2026-11-08 定），只有 type=choice 时有意义：
    #   key    必要分支 —— 必须走，直接决定进哪个结局
    #   flavor 非必要分支 —— 对话不同、氛围不同，最后汇合回主线。
    #          这类可以大量铺，专门用来给玩家「我在做选择」的手感，不改变结局。
    branch_kind: Literal['key', 'flavor'] | None = None
    bg: Background
    chars: list[Pose]
    lines: list[Line] = Field(min_length=1)
    choices: list[Choice] | None = None
    pos: Position
    gen: Generation
    # 逐节点审核结果：用户点过「通过 / 驳回」才有值，没审过就是空。
    # 生成流水线不碰这个字段，所以重跑一次不会把用户审过的结论抹掉。
    review: NodeReview | None = None

    @model_serializer(mode='wrap')
    def _drop_null_choices(self, handler):
        # 契约 §3.6 约束 3：ending 节点「没有 choices」。
        # 校验模型允许 choices=None 表示不适用，但序列化时不能吐 "choices": null，
        # 否则 engine/graph.py 的 `'choices' in n` / `n.get('choices', [])` 会误判。
        data = handler(self)
        if data.get('choices') is None:
            data.pop('choices', None)
        # 同理：没审过的节点不该吐 "review": null，前端只看「有没有这个键」。
        if data.get('review') is None:
            data.pop('review', None)
        return data

class Edge(Model):
    id: str
    from_: str = Field(alias='from')
    port: int = Field(ge=0)
    to: str

class ChapterView(Model):
    id: str
    ordinal: int
    act_id: str
    act_title: str
    title: str
    beats: str
    node_budget: int
    status: Literal['pending', 'writing', 'done']
    summary: str
    node_ids: list[str]

class Flag(Model):
    """全局旗标声明：选项的 effects 写它、condition 读它。"""
    type: Literal['bool', 'int']
    default: bool | int = False

class Graph(Model):
    version: Literal[1]
    project: Project
    characters: list[Character]
    nodes: list[Node]
    edges: list[Edge]
    chapters: list[ChapterView] | None = None
    # 全书唯一的风格圣经。为 None 表示还没定，生图前必须先定。
    style: Style | None = None
    # 旗标表：本阶段只做「effects 能存下并被播放器应用 + condition 决定选项显隐」。
    flags: dict[str, Flag] | None = None
