"""graph.json 的公开数据契约；mock 与后续真实生成共用。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Project(Model):
    id: str
    name: str
    resolution: tuple[int, int]
    created_at: str

class Character(Model):
    id: str
    name: str
    color: str
    ref_image: str
    voice_id: str

class Background(Model):
    asset: str
    prompt: str
    seed: int
    provider: str

class Pose(Model):
    id: str
    pose: str
    pos: Literal['left', 'center', 'right']
    mood: str

class Line(Model):
    who: str
    text: str = Field(min_length=1)
    voice: str | None
    dur: float = Field(gt=0, allow_inf_nan=False)

class Choice(Model):
    label: str
    to: str
    condition: str | None = None

class Position(Model):
    x: float
    y: float

class Generation(Model):
    model: str
    at: str
    cost: float = Field(ge=0)

class Node(Model):
    id: str
    type: Literal['start', 'scene', 'choice', 'ending']
    title: str
    status: Literal['pending', 'generating', 'done', 'failed']
    end_kind: Literal['true', 'bad', 'alt'] | None = None
    bg: Background
    chars: list[Pose]
    lines: list[Line] = Field(min_length=1)
    choices: list[Choice] | None = None
    pos: Position
    gen: Generation

class Edge(Model):
    id: str
    from_: str = Field(alias='from')
    port: int = Field(ge=0)
    to: str

class Graph(Model):
    version: Literal[1]
    project: Project
    characters: list[Character]
    nodes: list[Node]
    edges: list[Edge]
