"""接收项目请求，持久化生成模式与检查点，并用 SSE 回放进度。"""
import asyncio
import copy
import json
import os
import re
import shutil
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Request, Header
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .config import ROOT, MOCK
from . import settings as app_settings
from .contract import Graph
from .engine.pipeline import Pipeline
from .mock_data import skeleton
from .providers import registry
from .providers.base import ProviderError, redact
from .store import atomic_json, atomic_bytes, read_json
from .engine.checkpoint import Checkpoints
from .engine import repair as skeleton_repair
from .engine.journal import Journal
from .engine.graph import (GraphRejected, apply_graph, normalize_graph, public_graph, safe_asset,
                           start_id, validate_graph)
from .engine.export_mock import export_game, ensure_assets, take_missing_assets
from .engine.contracts import stage_report
from . import ui_presets

# 同一个 Unity 工程同时只能跑一个构建：两次并发构建会互相覆盖 Assets\StreamingAssets
UNITY_BUILD_LOCK = asyncio.Lock()

router = APIRouter(prefix='/api')

NODES_PER_MINUTE = 8.125
MAX_GENERATION_NODES = 2000

def now():
    """当前 UTC 时间戳（ISO 字符串），所有记录都统一用它。"""
    return datetime.now(timezone.utc).isoformat()


def gate_graph(graph, source):
    """D3 写入门的 HTTP 包装：任何落盘前都过 apply_graph；被拒时 422 + **全部** problems。"""
    try:
        return apply_graph(graph, source=source)
    except GraphRejected as exc:
        error = ProviderError('GRAPH_REJECTED', str(exc), source, 422)
        error.payload['problems'] = exc.problems
        raise error from exc

class CreateProject(BaseModel):
    name: str = Field(min_length=1, max_length=120)

class Params(BaseModel):
    model_config = ConfigDict(extra='forbid')
    writer_mode: Literal['skeleton', 'legacy'] = 'skeleton'
    node_count: int = Field(default=round(60 * NODES_PER_MINUTE), ge=4, le=MAX_GENERATION_NODES)
    # target_minutes 保留给内部换算（generation_body 会按 node_count 反算覆盖），
    # 前端不再暴露这个选项 —— 玩家只需要填节点数。
    target_minutes: int = Field(default=60, ge=1)
    # 分支分两类（主人 2026-11-08 定）：
    #   key_branches    必要分支总数，决定能有几个结局，最多 10 个
    #   flavor_branches 气氛分支总数，只换对话、到底汇合回主线，可以几十上百
    # branch_depth 是「嵌套几层」，和上面两个「数量」是两回事。
    key_branches: int = Field(default=2, ge=1, le=10)
    flavor_branches: int = Field(default=8, ge=0, le=200)
    branch_depth: int = Field(default=2, ge=0, le=8)
    # §4.3 骨架模式的形状参数：选择点密度、章数、结局数（flavor_branches 只在 legacy 分章模式起作用）。
    choice_density: float = Field(default=0.06, gt=0, le=0.2)
    chapters: int = Field(default=9, ge=1, le=60)
    endings: int = Field(default=4, ge=1, le=4)
    auto_voice: bool = True
    consistency: bool = True
    # 生成完之后进入逐节点过审：每个节点要用户点「通过」或「驳回并写理由」。
    # 结果写在 node.review 上，下次生成不会覆盖已通过的节点。
    review_per_node: bool = False
    # 收尾验证：全部跑完后由独立的质检 Agent 把成品查一遍，出判定和分数。
    # 它只读不写 —— 检查者不能顺手动刀子。
    final_check: bool = False
    # commit D：checkpoint 恢复 / 开工探图之后，图不合法且有骨架时按骨架修复。
    # 默认关 —— 不开就只在日志里提示「可以按骨架修复」，不改任何数据。
    repair_from_skeleton: bool = False
    resolution: tuple[int, int] = (1920, 1080)

class Generate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    requirement: str = Field(min_length=1, max_length=10000)
    params: Params = Field(default_factory=Params)

def minutes_for_nodes(count):
    """节点数换算成目标时长（分钟），口径要和生成接口一致。"""
    return max(1, round(count / NODES_PER_MINUTE))


def resume_signature(value):
    """断点续跑判定用的参数指纹。

    只认「会改变已落盘内容」的参数：
    - branch_depth 只限制**后续**章节能写几层嵌套，不影响已经写好的章节。
      把它算进去就变成「想放宽层级必须清空重写」（主人 2026-11-08 踩到）。
    """
    params = dict((value or {}).get('params') or {})
    params.pop('branch_depth', None)
    # 修复开关只决定「坏图要不要先按骨架修」，不改变已写内容的含义，不参与续跑判定。
    params.pop('repair_from_skeleton', None)
    return ((value or {}).get('requirement'), params)

def generation_body(body):
    """统一时长换算，并让升级前已经落盘的任务保留原来的编剧模式。"""
    params = dict(body['params'])
    params['target_minutes'] = minutes_for_nodes(params['node_count'])
    # 新请求的 model_dump 含 writer_mode，只有升级前的任务记录缺少这个字段。
    params.setdefault('writer_mode', 'legacy')
    return dict(body, params=params)

class Export(BaseModel):
    format: Literal['renpy', 'web', 'unity', 'video']

class UiDesign(BaseModel):
    """界面风格设计的请求体（打包页「界面风格」卡片提交的就是它）。

    两项都可以空着：没给要求时按项目题材挑最贴近的骨架，没给参考图时沿用预设配色。
    `base` 是可选覆盖，用户在界面上手动指定从哪套预设出发时才传。
    """
    requirement: str = ''
    references: list[str] = Field(default_factory=list)
    target: Literal['title', 'dialog', 'both'] = 'both'
    base: str = ''

class Regenerate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    parts: list[Literal['bg', 'char', 'voice']] = Field(default_factory=lambda: ['bg', 'char', 'voice'])

def configs_source():
    """真实 provider 配置来源。延迟导入 main，避免循环导入；测试可 patch 本函数。"""
    from .main import credentials
    return credentials.read()


def mock_notice():
    """mock 模式下给用户看的那句提示。"""
    return 'GAL_MOCK=1：使用内置模拟模型，不调用外部供应商'

class Jobs:
    def __init__(self, root=ROOT):
        """初始化任务状态：事件列表、状态、参数快照。"""
        self.root = Path(root)
        self.jobs = {}
        self.tasks = {}
        # 每个项目一份 Journal，按目录缓存 —— 序号要连续，不能每次 new 一个。
        self.journals = {}
        self.queue = asyncio.Lock()
        self.admission = asyncio.Lock()
        # 按骨架修复写盘前的备份目录（和 tools/repair_from_skeleton.py 同一个位置）
        self.backup_root = self.root / '_backup_projects'
        # 启动自检的结果：{项目id: hint}，只读诊断，不修
        self.repair_hints = {}
        self.delay = min(2.0, max(0.0, float(os.getenv('GAL_MOCK_STEP_DELAY', '0'))))
        self.reap_orphans()

    def reap_orphans(self):
        """把上一个进程遗留的 queued/running 任务标成已中断，并把 job 索引装回内存。

        为什么必须清：进程重启后内存里的 asyncio task 已经没了，但 .jobs 里的
        job 文件仍然是 running/queued。这个状态没有任何代码会去推进它，可
        前端会一直显示「排队中 / 进行中」——用户看到的就是卡死（主人实测）。
        run() 的注释写着「关闭服务时保留 running，下一次启动恢复」，但根本没有
        恢复逻辑，所以这里统一标成 failed（INTERRUPTED），让状态和现实一致。

        ★★ 还必须把 job 读回 self.jobs：
        generate() 判断「能不能断点续跑」时查的是 self.jobs.get(graph.agent_state.job_id)，
        **只看内存**。以前这里只改文件状态、不回填内存 → 后端一重启 previous 就是 None
        → retry=False → resume=False → skeleton() 把整张图清空从第一章重写。
        主人 2026-11-08 就是这么丢掉「了」这个项目的 7 章 / 135 节点的。
        events 一个就几十万字符，回填时清空（判定用不到）。
        """
        folder = self.root / '.jobs'
        if not folder.is_dir():
            return
        for path in folder.glob('*.json'):
            try:
                job = json.loads(path.read_text(encoding='utf-8'))
            except Exception:                                  # noqa: BLE001
                continue
            if isinstance(job, dict) and job.get('id'):
                self.jobs[job['id']] = dict(job, events=[])
            if job.get('status') not in ('queued', 'running'):
                continue
            job['status'] = 'failed'
            job['error'] = dict(
                code='INTERRUPTED',
                message='服务重启，这个任务已中断（已落盘的章节还在，可以直接续跑）',
                where='jobs.reap')
            if isinstance(job.get('id'), str) and job['id'] in self.jobs:
                self.jobs[job['id']] = dict(job, events=[])
            try:
                atomic_json(path, job)
            except Exception:                                  # noqa: BLE001
                pass

    def journal(self, root):
        """取某个项目的运行日志（按目录缓存，保证序号连续）。"""
        key = str(root)
        if key not in self.journals:
            self.journals[key] = Journal(root)
        return self.journals[key]

    async def recover_graph(self, root, job):
        """开工前先确认盘上的图是好的；坏了就从最近一份快照回滚。

        为什么必须在 read_json 之前做：图坏了 read_json 直接抛 STATE_CORRUPT，
        整次续跑连「能不能救」都没问就结束了。这里先探一次 —— 能救就先救，
        救不了再让它抛，那时候报错才有意义。
        """
        checkpoints = Checkpoints(root)
        probe = await asyncio.to_thread(checkpoints.verify_file)
        if probe['status'] != 'corrupt':
            await self.skeleton_repair_step(root, job, probe.get('graph'))
            return probe
        found = await asyncio.to_thread(checkpoints.recover)
        journal = self.journal(root)
        if found is None:
            await self.emit(job, 'log', dict(level='error', text=(
                f"graph.json 读不出来，也没有可用快照：{probe['reason']}")))
            return probe
        # 坏现场先留一份再覆盖 —— 那是唯一能追查「为什么会坏」的证据
        stamp = time.strftime('%Y%m%d-%H%M%S')
        broken = root / ('graph.broken-%s.json' % stamp)
        try:
            await asyncio.to_thread(shutil.copy2, root / 'graph.json', broken)
        except OSError:
            broken = None
        snapshot = found['graph']
        repaired = None
        if self.wants_skeleton_repair(job):
            info, built = await asyncio.to_thread(skeleton_repair.report, snapshot)
            if built is not None and info['repairable']:
                snapshot, repaired = built, info
        else:
            await self.emit_repair_hint(job, root, snapshot)
        restored = gate_graph(snapshot, 'checkpoint.restore')
        if repaired is not None and (root / 'graph.json').is_file():
            repaired['backup'] = str(await asyncio.to_thread(
                skeleton_repair.backup_graph, root / 'graph.json', self.backup_root, root.name))
        await asyncio.to_thread(atomic_json, root / 'graph.json', restored)
        if repaired is not None:
            await self.emit(job, 'log', dict(level='warn', text=(
                f"快照按骨架修复后写回：结构问题 {repaired['problems_before']}→0，"
                f"断头 {repaired['dead_ends_before']}→{repaired['dead_ends_after']}，"
                f"备份 {repaired.get('backup')}")))
        journal.append(job=job['id'], agent='pipeline', phase='graph_recovered',
                       note=f"图损坏({probe['reason'][:80]})，回滚到快照 #{found['meta']['seq']}")
        await self.emit(job, 'log', dict(level='warn', text=(
            f"graph.json 损坏（{probe['reason'][:60]}），已回滚到快照 #{found['meta']['seq']}"
            f"（{found['meta'].get('nodes')} 节点）"
            + (f"；坏文件留了一份 {broken.name}" if broken else ''))))
        return probe

    @staticmethod
    def wants_skeleton_repair(job):
        """显式开关：job.body.params.repair_from_skeleton，默认 False。"""
        return bool((((job or {}).get('body') or {}).get('params') or {}).get('repair_from_skeleton'))

    async def emit_repair_hint(self, job, root, graph):
        """图不合法且有骨架：只提示「可以按骨架修复」，不修、不写。"""
        if not isinstance(graph, dict):
            return None
        hint = await asyncio.to_thread(skeleton_repair.repair_hint, graph)
        if hint is not None:
            await self.emit(job, 'log', dict(level='warn', text=(
                f"图有 {hint['problems']} 项结构问题（{hint['dead_ends']} 个断头），且有骨架：{hint['hint']}。"
                f"生成参数 repair_from_skeleton=true 或 POST /api/projects/{root.name}/repair-from-skeleton"
                f" {{\"apply\": true}}")))
        return hint

    async def skeleton_repair_step(self, root, job, graph):
        """开工探图之后的可选步骤：开关开了才修（严格闸 + 写前备份），没开只提示。"""
        if not isinstance(graph, dict) or not skeleton_repair.has_skeleton(graph):
            return None
        if not self.wants_skeleton_repair(job):
            return await self.emit_repair_hint(job, root, graph)
        info = await asyncio.to_thread(skeleton_repair.repair_project, root,
                                       backup_root=self.backup_root, apply=True,
                                       source='checkpoint.repair')
        if info['applied']:
            await self.emit(job, 'log', dict(level='warn', text=(
                f"已按骨架修复：结构问题 {info['problems_before']}→0，"
                f"断头 {info['dead_ends_before']}→{info['dead_ends_after']}，备份 {info['backup']}")))
        elif info['problems_before']:
            await self.emit(job, 'log', dict(level='error', text=(
                f"按骨架修复不可行：{info.get('reason') or '修复后仍有 %s 项问题' % info.get('problems_after')}")))
        return info

    def projects_root(self):
        """项目数据目录。跟着设置走：设置里改了保存位置，这里就换。"""
        return app_settings.projects_dir()

    def project_path(self, project_id):
        """校验项目 id 合法并返回它的目录。"""
        if not re.fullmatch(r'p_[0-9a-f]{32}', project_id):
            raise ProviderError('PROJECT_NOT_FOUND', '项目不存在', 'projects.get', 404)
        path = self.projects_root() / project_id
        if not (path / 'project.json').is_file():
            raise ProviderError('PROJECT_NOT_FOUND', '项目不存在', 'projects.get', 404)
        return path

    def job_path(self, job_id):
        """任务记录文件的路径（.jobs/<id>.json）。"""
        return self.root / '.jobs' / (job_id + '.json')

    def get(self, job_id):
        """按 id 取任务；不存在返回 None。"""
        if job_id not in self.jobs:
            raise ProviderError('JOB_NOT_FOUND', '任务不存在', 'jobs.get', 404)
        return self.jobs[job_id]

    async def persist(self, writer, path, value):
        """把任务状态写到 .jobs/<id>.json，重启后才能恢复。"""
        write = asyncio.create_task(asyncio.to_thread(writer, path, value))
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            await write
            raise

    async def save(self, job):
        """同 persist（旧名字，保留兼容）。"""
        await self.persist(atomic_json, self.job_path(job['id']), job)

    async def emit_many(self, job, batch, **updates):
        """批量推事件（比逐条推少写几次盘）。"""
        events = [dict(id=len(job['events']) + offset, event=event, data=copy.deepcopy(data))
                  for offset, (event, data) in enumerate(batch, 1)]
        snapshot = dict(job, **updates)
        snapshot['events'] = job['events'] + events
        await self.save(snapshot)
        job.update(snapshot)

    async def emit(self, job, event, data, **updates):
        """推一条事件：追加到列表 + 落盘 + 唤醒事件流。"""
        await self.emit_many(job, [(event, data)], **updates)
        if event in ('log', 'error'):
            path = self.project_path(job['project_id']) / 'logs' / 'run.log'
            text = data.get('text', data.get('message', ''))
            level = data.get('level', 'error')
            def append():
                """往事件列表追加一条，顺便通知等着的消费者。"""
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open('a', encoding='utf-8') as stream:
                        stream.write(f'[{now()}] [{level}] [job] {text}\n')
                except OSError as exc:
                    raise ProviderError('STATE_NOT_WRITABLE', f'{path}: {type(exc).__name__}: {exc}', 'jobs.log', 500) from exc
            await asyncio.to_thread(append)
            # 人读的 run.log 和机读的 journal 各记一份：前者看现场，后者看序列。
            self.journal(path.parent.parent).append(
                job=job['id'], agent=job.get('active_agent'), phase='log',
                level=level, note=text[:200])

    async def agent(self, job, agent, state, progress, msg):
        """推一条 agent 进度事件。"""
        await self.emit(job, 'agent', dict(agent=agent, state=state, progress=progress, msg=msg))

    def require_idle(self, project_id):
        """没有任务在跑时才允许写图 —— 防并发写坏。"""
        if any(j['project_id'] == project_id and j['status'] in ('queued', 'running')
               for j in self.jobs.values()):
            raise ProviderError('PROJECT_BUSY', '项目已有运行任务', 'jobs.submit', 409)

    async def submit(self, project_id, kind, body, *, warning=None, exported=False):
        """新建一个任务并排队。"""
        async with self.admission:
            self.project_path(project_id)
            self.require_idle(project_id)
            job_id = 'j_' + uuid.uuid4().hex
            job = dict(id=job_id, project_id=project_id, kind=kind, body=body, status='queued',
                       events=[], stages=[], started=now(), mock_mode=MOCK)
            if warning:
                job['warning'] = warning
            if exported:
                job['exported'] = True
            await self.save(job)
            self.jobs[job_id] = job
            self.tasks[job_id] = asyncio.create_task(self.run(job))
            return job

    async def run(self, job):
        """任务主循环：跑流水线、写事件、收尾改状态。"""
        async with self.queue:
            try:
                job['status'] = 'running'
                await self.save(job)
                if job['kind'] == 'generate':
                    await self.generate(job)
                elif job['kind'] == 'regenerate':
                    await self.regenerate(job)
                else:
                    await self.export(job)
            except asyncio.CancelledError:
                # 关闭服务时保留 running，下一次启动恢复；显式取消由接口写终态。
                raise
            except Exception as exc:
                error = dict(exc.payload) if isinstance(exc, ProviderError) else dict(
                    code='JOB_FAILED', message=f'{type(exc).__name__}: {exc}', where='jobs.' + job['kind'])
                error['message'] = redact(error['message'], {})
                # 磁盘故障也必须让在线 SSE 收到终态，不能无限等待。
                try:
                    await self.agent(job, job.get('active_agent', 'build'), 'failed', 0.0, error['message'])
                    await self.emit(job, 'error', error, status='failed')
                except Exception:
                    if not job['events'] or job['events'][-1]['event'] != 'error':
                        job['events'].append(dict(id=len(job['events']) + 1, event='error', data=error))
                finally:
                    job['status'] = 'failed'
                    try:
                        await self.save(job)
                    except Exception:
                        pass  # 原始磁盘错误已进入内存事件，SSE 必须能结束。

    async def generate(self, job):
        """生成任务的主体：装配流水线并跑完整个 writer→build。"""
        root = self.project_path(job['project_id'])
        mock = job.get('mock_mode', MOCK)
        job['body'] = generation_body(job['body'])
        # 先探一次盘上的图：坏了就地回滚，别让 read_json 直接抛死整次续跑。
        await self.recover_graph(root, job)
        graph = await asyncio.to_thread(read_json, root / 'graph.json')
        checkpoint = graph.get('agent_state', {}).get('job_id')
        previous = self.jobs.get(checkpoint)
        retry = (previous is not None and previous['status'] in ('failed', 'cancelled') and
                 previous.get('mock_mode', MOCK) == mock and
                 resume_signature(generation_body(previous['body'])) == resume_signature(job['body']))
        resume = checkpoint == job['id'] or retry
        # 图内容和最近一份检查点对不对得上 —— 这是「盘上这份是真的吗」的唯一依据。
        probe = Checkpoints(root).verify(graph)
        self.journal(root).append(
            job=job['id'], agent='pipeline', phase='resume_check',
            note=f'resume={resume} retry={retry} checkpoint={probe["status"]} '
                 f'nodes={probe.get("nodes")} edges={probe.get("edges")}')

        async def save_graph(value):
            """把图落盘（注入给 pipeline，测试时可换成内存实现）。先过写入门，edges 以重算为准。"""
            checked = gate_graph(value, 'pipeline.save')
            value['edges'] = checked['edges']
            await self.persist(atomic_json, root / 'graph.json', checked)

        async def save_asset(relative, data):
            """把素材落盘（同上）。"""
            await self.persist(atomic_bytes, root / relative, data)

        if not resume:
            # ★ 参数和上次不一致 → 会清空整张图从第一章重写。
            #   以前是直接抹掉：实测把「了」这个项目的 7 章 135 节点清成了 0
            #   （主人 2026-11-08 22:20 踩到）。现在清空前先落一份快照，出事能捞回来。
            existing_nodes = len(graph.get('nodes') or [])
            done_count = sum(1 for act in (graph.get('agent_state') or {}).get('acts', [])
                             for chapter in act.get('chapters', [])
                             if chapter.get('status') == 'done')
            if existing_nodes > 1 or done_count:
                stamp = time.strftime('%Y%m%d-%H%M%S')
                backup = root / ('graph.before-restart-%s.json' % stamp)
                await asyncio.to_thread(atomic_json, backup, graph)
                await self.emit(job, 'log', dict(
                    level='warn',
                    text=(f'本次参数与上次不一致，会清空重写（原有 {done_count} 章 / {existing_nodes} 节点）。'
                          f'清空前的图已备份到 {backup.name}')))
            project = await asyncio.to_thread(read_json, root / 'project.json')
            project['resolution'] = job['body']['params']['resolution']
            graph = skeleton(project)
            if job['body']['params']['writer_mode'] == 'legacy':
                graph['nodes'] = graph['nodes'][:1]
                graph['edges'] = []
                start = graph['nodes'][0]
                opening = '故事从这里开始'
                start.update(status='pending', choices=[],
                             lines=[dict(who='', text=opening, voice=None, dur=round(len(opening) / 4.5, 3))])
                start['bg']['provider'] = 'pending'
            else:
                # 等待模型时仍保留有出口的项目初始图，整图校验通过后一次替换。
                for node in graph['nodes']:
                    node['status'] = 'pending'
            graph['agent_state'] = {}
        elif retry and 'writer' not in graph['agent_state'].get('pipeline', {}).get('completed', ()):
            graph['agent_state'].pop('writer_published', None)
        if checkpoint != job['id']:
            graph['agent_state']['job_id'] = job['id']
            await save_graph(graph)
        if resume:
            detail = f'检查点：{probe["status"]}'
            if probe.get('nodes_delta'):
                detail += f'，比上次检查点多 {probe["nodes_delta"]} 个节点'
            if probe.get('status') == 'regressed':
                detail += '（有内容比检查点少，编剧会自己看证据判）'
            await self.emit(job, 'log', dict(level='info', text=f'从已落盘的生成检查点恢复（{detail}）'))
        if job.get('warning'):
            await self.emit(job, 'log', dict(level='warn', text=job['warning']))

        configs = {}
        if mock:
            plan = dict(providers={}, degraded=dict.fromkeys(('writer', 'art', 'voice'), mock_notice()))
            await self.emit(job, 'log', dict(level='info', text=mock_notice()))
        else:
            try:
                configs = await asyncio.to_thread(configs_source)
            except Exception as exc:
                await self.emit(job, 'log', dict(
                    level='warn', text=f'读取 provider 配置失败：{type(exc).__name__}'))
            plan = registry.plan(configs)

        async def emit(event, data):
            """推一条事件（注入给 pipeline 的回调）。"""
            updates = {}
            if event == 'agent':
                updates['active_agent'] = data['agent']
            if event == 'log' and configs:
                data = dict(data, text=redact(data['text'], configs))
            if event == 'done':
                stats = data['stats']
                started = datetime.fromisoformat(job['started'])
                stats['elapsed_s'] = round((datetime.now(timezone.utc) - started).total_seconds(), 3)
                updates.update(status='done', stats=stats, mock=stats['mock'],
                               stages=list(stats['stages']))
            await self.emit(job, event, data, **updates)

        async def emit_many(batch):
            """批量推事件（注入给 pipeline 的回调）。"""
            await self.emit_many(job, batch)

        pipeline = Pipeline(graph=graph, body=job['body'], root=root,
                            providers=plan['providers'], emit=emit, emit_many=emit_many,
                            save_graph=save_graph, save_asset=save_asset, delay=self.delay,
                            mock=mock, degraded=plan['degraded'])
        try:
            await pipeline.run()
        except ProviderError as exc:
            exc.payload['message'] = redact(exc.payload['message'], configs)
            raise

    async def regenerate(self, job):
        """单节点重生成：mock 只发事件；真模式重画背景 / 重抠立绘 / 重合成语音。

        复用 AgentTools 的现有工具（draft_prompt + generate_image 走风格圣经与素材库，
        _synthesize 走单句 TTS），不另写一套。
        """
        started = time.monotonic()
        root = self.project_path(job['project_id'])
        graph = await asyncio.to_thread(read_json, root / 'graph.json')
        node_id = job['body']['node_id']
        parts = job['body']['parts']
        node = next((n for n in graph['nodes'] if n['id'] == node_id), None)
        if node is None:
            raise ProviderError('NODE_NOT_FOUND', f'节点不存在: {node_id}', 'regenerate.start', 404)

        if job.get('mock_mode', MOCK):
            await self.emit(job, 'log', dict(level='info',
                                             text=f'GAL_MOCK=1：单节点重生成 {node_id}，parts={parts}；不调用模型。'))
            if 'bg' in parts or 'char' in parts:
                job['active_agent'] = 'art'
                await self.agent(job, 'art', 'run', 0.0, '重写占位背景与立绘')
                await asyncio.sleep(self.delay)
                await self.agent(job, 'art', 'done', 1.0, '占位素材完成')
            if 'voice' in parts:
                job['active_agent'] = 'voice'
                await self.agent(job, 'voice', 'run', 0.0, '重写静音语音')
                await asyncio.sleep(self.delay)
                await self.agent(job, 'voice', 'done', 1.0, '静音语音完成')
            node['status'] = 'done'
            await asyncio.to_thread(atomic_json, root / 'graph.json', gate_graph(graph, 'api.regenerate'))
            await self.emit(job, 'node', dict(node_id=node_id, patch=copy.deepcopy(node)))
            await self.emit(job, 'done', dict(stats=dict(
                nodes=len(graph['nodes']), edges=len(graph['edges']), images=1,
                voices=sum(bool(line['voice']) for line in node['lines']),
                cost=0.0, elapsed_s=round(time.monotonic() - started, 3))), status='done')
            return

        from hashlib import sha256
        from .agents.tools import AgentTools, ToolInputError
        from .assets.cutout import remove_white_background
        from .engine.graph import safe_asset

        async def save_graph(value):
            """把图落盘（注入给 pipeline，测试时可换成内存实现）。先过写入门，edges 以重算为准。"""
            checked = gate_graph(value, 'pipeline.save')
            value['edges'] = checked['edges']
            await self.persist(atomic_json, root / 'graph.json', checked)

        async def save_asset(relative, data):
            """把素材落盘（同上）。"""
            await self.persist(atomic_bytes, root / relative, data)

        configs = {}
        try:
            configs = await asyncio.to_thread(configs_source)
        except Exception as exc:                      # noqa: BLE001
            await self.emit(job, 'log', dict(level='warn',
                                             text=f'读取 provider 配置失败：{type(exc).__name__}'))
        plan = registry.plan(configs)
        pipeline_state = graph.get('agent_state', {}).get('pipeline') or {}
        params = dict(pipeline_state.get('params') or {})
        params.setdefault('resolution', tuple(graph['project']['resolution']))
        params.setdefault('auto_voice', True)
        params.setdefault('consistency', False)
        tools = AgentTools(graph, dict(params=params), root, providers=plan['providers'],
                           save_graph=save_graph, save_asset=save_asset)

        images = voices = 0
        notes = []
        try:
            if 'bg' in parts:
                job['active_agent'] = 'art'
                await self.agent(job, 'art', 'run', 0.0, '重画背景')
                meta = tools.node_meta.get(node_id) or {}
                key = meta.get('bg_key') or node['bg'].get('asset') or node_id
                brief = node['bg'].get('prompt') or node.get('title') or key
                group = [n['id'] for n in (tools._backgrounds().get(key) or [])]
                notes.append(f'背景分组 {key}：{len(group)} 个（{",".join(group[:8])}…）')
                # 强制重画：generate_image 看到「文件存在且 provider 不是 pending」会直接复用，
                # 重生成必须先把来源标回 pending。
                node['bg']['provider'] = 'pending'
                draft = await tools.dispatch('art', 'draft_prompt',
                                             dict(target='background', key=key, brief=brief))
                if not draft['ok']:
                    raise ProviderError(draft['error'], draft['message'], 'regenerate.bg')
                got = await tools.dispatch('art', 'generate_image',
                                           dict(target='background', key=key,
                                                prompt=draft['prompt'],
                                                negative=draft.get('negative', '') or '',
                                                force=True))
                if not got['ok']:
                    raise ProviderError(got['error'], got['message'], 'regenerate.bg')
                images += 1
                if got.get('generated'):
                    notes.append('背景已重画：' + str(got.get('asset')))
                else:
                    notes.append('背景未变（' + str(got.get('reused') or '同样内容') +
                                 '）：' + str(got.get('asset')))
                await self.agent(job, 'art', 'done', 1.0, '背景完成')

            if 'char' in parts:
                job['active_agent'] = 'art'
                await self.agent(job, 'art', 'run', 0.0, '重抠立绘')
                # ★ 一定要取 graph 里的**当前**节点：重画背景那一步会把它换成新对象，
                #   用函数开头抓的旧 node 去改 chars，再把整个 list 提交回去，
                #   会把刚画好的新背景一起冲回旧值（实测踩过：图生成了但节点没变）。
                target_node = next((n for n in graph['nodes'] if n['id'] == node_id), node)
                cut = 0
                for pose in target_node.get('chars') or []:
                    relative = pose.get('pose') or ''
                    if not relative:
                        continue
                    try:
                        path = safe_asset(root, relative)
                    except (FileNotFoundError, ValueError):
                        continue
                    if not path.is_file():
                        continue
                    raw = path.read_bytes()
                    data = await asyncio.to_thread(remove_white_background, raw)
                    # 重抠也要保持中文名。以前这里拼的是 sha256 前 20 位，
                    # 用户在素材库里点一次「重抠」，看得懂的文件名就打回哈希了。
                    old_stem = relative.rsplit('/', 1)[-1].rsplit('.', 1)[0]
                    if old_stem[:2].isdigit() and old_stem[2:3] == '-':
                        old_stem = old_stem[3:]
                    new_rel, _written = await tools._save_named('assets/char', old_stem, data)
                    if new_rel != relative:
                        pose['pose'] = new_rel
                        cut += 1
                if cut:
                    images += cut
                    notes.append(f'{cut} 张立绘已重抠')
                    await tools._commit(nodes=list(graph['nodes']))
                else:
                    notes.append('立绘已经是透明图，无需重抠')
                await self.agent(job, 'art', 'done', 1.0, '立绘完成')

            if 'voice' in parts:
                job['active_agent'] = 'voice'
                await self.agent(job, 'voice', 'run', 0.0, '重合成语音')
                for index in range(len(node['lines'])):
                    try:
                        line, generated = await tools._synthesize(node, index, overwrite=True)
                    except (ToolInputError, ProviderError) as exc:
                        message = str(exc) if isinstance(exc, ToolInputError) else exc.payload.get('message', '')
                        notes.append(f'第 {index + 1} 句失败：{message[:80]}')
                        continue
                    if generated:
                        node['lines'][index] = line
                        voices += 1
                await self.agent(job, 'voice', 'done', 1.0, '语音完成')
        except ProviderError as exc:
            exc.payload['message'] = redact(exc.payload['message'], configs)
            raise

        if 'char' in parts and images:
            await tools._commit(nodes=list(graph['nodes']))
        node['status'] = 'done'
        await save_graph(graph)
        await self.emit(job, 'node', dict(node_id=node_id, patch=copy.deepcopy(node)))
        if notes:
            await self.emit(job, 'log', dict(level='info', text='；'.join(notes)))
        await self.emit(job, 'done', dict(stats=dict(
            nodes=len(graph['nodes']), edges=len(graph['edges']),
            images=images, voices=voices, cost=0.0,
            elapsed_s=round(time.monotonic() - started, 3))), status='done')

    async def export(self, job):
        root = self.project_path(job['project_id'])
        graph = await asyncio.to_thread(read_json, root / 'graph.json')
        await asyncio.to_thread(validate_graph, graph, root, False)
        job['active_agent'] = 'build'
        if job.get('warning'):
            await self.emit(job, 'log', dict(level='warn', text=job['warning']))
        await self.agent(job, 'build', 'run', 0.0, '开始导出占位素材工程')
        if not job.get('exported'):
            try:
                await asyncio.to_thread(export_game, root, graph, job['body']['format'])
            except OSError as exc:
                raise ProviderError('STATE_NOT_WRITABLE', f'{root / "export"}: {type(exc).__name__}: {exc}', 'export.write', 500) from exc
        await self.agent(job, 'build', 'done', 1.0, '导出完成')
        stats = dict(nodes=len(graph['nodes']), edges=len(graph['edges']), images=3,
                     voices=sum(bool(line['voice']) for node in graph['nodes'] for line in node['lines']),
                     cost=0.0, elapsed_s=0.0, pipeline=stage_report(graph))
        await self.emit(job, 'done', dict(stats=stats), status='done')

    async def restore(self):
        """重启后从 .jobs 恢复未完成任务 —— 断点续跑的关键。"""
        for path in (self.root / '.jobs').glob('j_*.json'):
            job = await asyncio.to_thread(read_json, path)
            self.jobs[job['id']] = job
        for job in self.jobs.values():
            if job['status'] not in ('queued', 'running'):
                continue
            if job['events'] and job['events'][-1]['event'] == 'done':
                job['status'] = 'done'
                await self.save(job)
            else:
                self.tasks[job['id']] = asyncio.create_task(self.run(job))


jobs = Jobs()

@asynccontextmanager
async def lifespan(app):
    """FastAPI 的启动/关闭钩子：启后台线程、恢复上次没跑完的任务。"""
    await jobs.restore()
    scan = asyncio.create_task(startup_self_check())
    yield
    scan.cancel()
    for task in jobs.tasks.values():
        task.cancel()
    await asyncio.gather(*jobs.tasks.values(), return_exceptions=True)

_SELF_CHECKED = False


async def startup_self_check():
    """启动自检：图不合法且有骨架的项目在日志里提示「可以按骨架修复」。只读，不修。

    后台线程跑，不拖慢启动；同一进程只扫一次（测试里 TestClient 反复进出 lifespan）。
    """
    global _SELF_CHECKED
    if _SELF_CHECKED or os.getenv('GAL_SKIP_SELF_CHECK'):
        return
    _SELF_CHECKED = True
    try:
        jobs.repair_hints = await asyncio.to_thread(skeleton_repair.scan_hints, jobs.projects_root())
    except Exception as exc:                               # noqa: BLE001
        skeleton_repair.log.warning('启动自检失败：%s: %s', type(exc).__name__, exc)


class RepairIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    apply: bool = False


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    projects_dir: str = ''


@router.get('/settings')
async def read_settings_api():
    """本机偏好设置（目前主要是一项：项目数据放哪）。"""
    cfg = app_settings.read()
    return dict(
        ok=True,
        projects_dir=str(app_settings.projects_dir()),
        projects_dir_override=str(cfg.get('projects_dir') or ''),
        default_projects_dir=str(app_settings.ROOT / 'projects'),
        data_dir=str(app_settings.data_dir()),
        settings_file=str(app_settings.settings_file()),
        app_root=str(app_settings.ROOT),
    )


@router.post('/settings')
async def save_settings_api(body: SettingsIn):
    """保存本机设置（项目目录等），返回保存后的全量设置。"""
    raw = (body.projects_dir or '').strip()
    if raw:
        target = Path(raw)
        if not target.is_absolute():
            raise ProviderError('SETTINGS_INVALID',
                                '项目保存位置要填绝对路径，例如 D:\\GalgameProjects',
                                'settings.save', 400)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProviderError('SETTINGS_INVALID', f'这个目录建不出来：{exc}',
                                'settings.save', 400) from exc
    cfg = app_settings.read()
    cfg['projects_dir'] = raw
    await asyncio.to_thread(app_settings.write, cfg)
    return dict(ok=True, projects_dir=str(app_settings.projects_dir()),
                restart_required=True,
                note='保存位置已改，重启「奇迹工坊」后生效')


@router.post('/projects')
async def create_project(body: CreateProject):
    """新建项目目录并写入初始 graph.json。"""
    project_id = 'p_' + uuid.uuid4().hex
    root = jobs.projects_root() / project_id
    project = dict(id=project_id, name=body.name, resolution=[1920, 1080], created_at=now())
    # ★ 给一个起点→结局的骨架，不是空图：空图前端打不开也没法编辑（见 skeleton 注释）
    await asyncio.to_thread(atomic_json, root / 'graph.json', gate_graph(skeleton(project), 'api.create'))
    await asyncio.to_thread(atomic_json, root / 'project.json', project)
    return dict(id=project_id, path=str(root))

def _list_projects():
    """项目列表：把列表页要显示的东西一趟算完。

    ★ 不能只把 project.json 原样吐出去 —— 那样前端要显示"几个节点、几个结局"
      就得给每个项目再拉一次 /graph（现在磁盘上有 53 个项目 = 53 个请求）。
      这里在同一趟遍历里顺便读 graph.json、算更新时间。
    """
    out = []
    base = jobs.projects_root()
    if not base.is_dir():
        return out
    for meta_path in base.glob('p_*/project.json'):
        try:
            meta = read_json(meta_path)
        except Exception:
            continue                      # 坏掉的单个项目不能拖垮整个列表
        root = meta_path.parent
        item = dict(meta)

        nodes = []
        try:
            g = read_json(root / 'graph.json')
            nodes = g.get('nodes') or []
        except Exception:
            pass
        item['node_count'] = len(nodes)
        item['ending_count'] = sum(1 for n in nodes if n.get('type') == 'ending')
        item['choice_count'] = sum(1 for n in nodes if n.get('type') == 'choice')

        stamps = []
        for q in (meta_path, root / 'graph.json', root / 'project.json'):
            try:
                stamps.append(q.stat().st_mtime)
            except OSError:
                pass
        item['updated_at'] = (
            datetime.fromtimestamp(max(stamps), timezone.utc).isoformat()
            if stamps else item.get('created_at'))

        try:
            item['has_export'] = (root / 'export').is_dir() and any((root / 'export').iterdir())
        except OSError:
            item['has_export'] = False

        out.append(item)

    # 最近动过的排前面 —— 用户找的几乎总是刚做的那个
    out.sort(key=lambda x: x.get('updated_at') or '', reverse=True)
    return out


@router.get('/projects')
async def list_projects():
    """列出所有项目（按最近改动排序），带节点数 / 结局数等统计。"""
    return await asyncio.to_thread(_list_projects)


@router.delete('/projects/{project_id}')
async def delete_project(project_id: str):
    """删项目（连整个目录一起删，含 assets / export / logs）。"""
    root = jobs.project_path(project_id)      # 内部已校验 p_+32位hex，防路径穿越
    if not root.is_dir():
        raise ProviderError('PROJECT_NOT_FOUND', '项目不存在', 'projects.delete', 404)
    if any(j['project_id'] == project_id and j['status'] in ('queued', 'running')
           for j in jobs.jobs.values()):
        raise ProviderError('PROJECT_BUSY', '项目正在生成，先取消任务再删', 'projects.delete', 409)
    await asyncio.to_thread(shutil.rmtree, root, True)
    return dict(ok=True, id=project_id)

@router.get('/projects/{project_id}/graph')
async def get_graph(project_id: str):
    """返回项目图；把 agent_state 里的章节计划拼成前端要的 chapters。"""
    root = jobs.project_path(project_id)
    graph = await asyncio.to_thread(read_json, root / 'graph.json')
    # D1：返回给客户端的 edges 一律是刚按 choices 重算的那份，磁盘值不出门。
    view = normalize_graph(public_graph(graph))
    state = graph.get('agent_state', {})
    acts = state.get('acts', [])
    if acts:
        members = defaultdict(list)
        for nid, meta in state.get('nodes', {}).items():
            members[meta['chapter_id']].append(nid)
        root_id = start_id(graph['nodes'])
        if root_id is not None and root_id not in state.get('nodes', {}):
            members[acts[0]['chapters'][0]['id']].insert(0, root_id)
        view['chapters'] = [dict(chapter, act_id=act['id'], act_title=act['title'],
                                 node_ids=members[chapter['id']])
                            for act in acts for chapter in act['chapters']]
    return view

@router.get('/projects/{project_id}/resume')
async def get_resume(project_id: str):
    """续写状态：图对不对得上检查点、上次跑到哪、有没有异常。

    只读，什么都不改 —— 前端在用户点「继续写」之前先展示这份状态。
    `anomalies` 是给人和模型看的事实：状态和内容对不上的地方。
    """
    root = jobs.project_path(project_id)
    checkpoints = Checkpoints(root)
    probe = await asyncio.to_thread(checkpoints.verify_file)
    graph = probe.pop('graph', None)
    summary = await asyncio.to_thread(checkpoints.summary)
    state = (graph or {}).get('agent_state') or {}
    meta = state.get('nodes') or {}
    chapters = [dict(id=chapter['id'], title=chapter.get('title'),
                     status=chapter.get('status'), ordinal=chapter.get('ordinal'),
                     node_budget=chapter.get('node_budget'),
                     nodes=sum(1 for item in meta.values()
                               if item.get('chapter_id') == chapter['id']))
                for act in state.get('acts', []) for chapter in act['chapters']]
    return dict(
        checkpoint=probe,
        snapshot=summary,
        chapters=chapters,
        done=[item['id'] for item in chapters if item['status'] == 'done'],
        writing=[item['id'] for item in chapters if item['status'] == 'writing'],
        plan=state.get('resume_plan'),
        journal=[{key: value for key, value in item.items() if key != 'digest'}
                 for item in jobs.journal(root).tail(20)],
        writable=probe['status'] != 'corrupt',
        repair=skeleton_repair.repair_hint(graph) if isinstance(graph, dict) else None,
    )


@router.get('/projects/{project_id}/repair-from-skeleton')
async def preview_skeleton_repair(project_id: str):
    """按骨架修复的预览（dry-run）：修复前后问题数、断头数、可见选项文字丢失。不写盘。"""
    root = jobs.project_path(project_id)
    info = await asyncio.to_thread(skeleton_repair.repair_project, root,
                                   backup_root=jobs.backup_root, apply=False)
    return info


@router.post('/projects/{project_id}/repair-from-skeleton')
async def run_skeleton_repair(project_id: str, body: RepairIn):
    """按骨架修复。默认 apply=false 只预览；apply=true 才写：严格闸通过 → 备份 → 原子写。"""
    root = jobs.project_path(project_id)
    async with jobs.admission:
        jobs.require_idle(project_id)
        try:
            info = await asyncio.to_thread(skeleton_repair.repair_project, root,
                                           backup_root=jobs.backup_root, apply=body.apply,
                                           source='api.repair')
        except GraphRejected as exc:
            error = ProviderError('GRAPH_REJECTED', str(exc), 'api.repair', 422)
            error.payload['problems'] = exc.problems
            raise error from exc
    if info.get('applied'):
        jobs.repair_hints.pop(project_id, None)
    return info

@router.post('/projects/{project_id}/generate')
async def generate(project_id: str, body: Generate):
    """提交一次生成任务：校验参数、换算节点数、排队或续跑。"""
    if any(not 64 <= x <= 8192 for x in body.params.resolution):
        raise ProviderError('INVALID_REQUEST', 'resolution 每边须为 64–8192', 'generate.validation', 422, field='params.resolution')
    payload = body.model_dump(mode='json')
    fields = body.params.model_fields_set
    requested_minutes = body.params.target_minutes
    warning = None
    if 'node_count' not in fields:
        if requested_minutes > int(MAX_GENERATION_NODES / NODES_PER_MINUTE):
            raise ProviderError('INVALID_REQUEST', f'目标时长换算后超过 {MAX_GENERATION_NODES} 个节点',
                                'generate.validation', 422, field='params.target_minutes')
        payload['params']['node_count'] = round(requested_minutes * NODES_PER_MINUTE)
    elif 'target_minutes' in fields:
        derived = minutes_for_nodes(body.params.node_count)
        if requested_minutes != derived:
            warning = (f'target_minutes={requested_minutes} 与 node_count={body.params.node_count} 不一致，'
                       f'按节点数生成，记录时长为 {derived} 分钟')
    # 先算「这次能不能真跑」，让 POST 响应就能看到 mock / 降级原因（不用等 SSE）。
    if MOCK:
        preview = dict(mock=True, degraded={'all': mock_notice()})
    else:
        try:
            configs = await asyncio.to_thread(configs_source)
        except Exception as exc:
            configs = {}
            preview = dict(mock=True, degraded={'config': f'读取 provider 配置失败：{type(exc).__name__}: {exc}'})
        else:
            plan = registry.plan(configs)
            preview = dict(mock=plan['mock'], degraded=plan['degraded'])
    job = await jobs.submit(project_id, 'generate', generation_body(payload), warning=warning)
    return dict(job_id=job['id'], sse=f'/api/jobs/{job["id"]}/events', **preview)

@router.get('/jobs/{job_id}/events')
async def events(job_id: str, request: Request, last_event_id: str | None = Header(default=None)):
    """SSE 事件流：把某个任务的事件实时推给前端。"""
    job = jobs.get(job_id)
    try:
        cursor = int(last_event_id or '0')
        if cursor < 0 or cursor > len(job['events']):
            raise ValueError()
    except ValueError as exc:
        raise ProviderError('INVALID_EVENT_ID', 'Last-Event-ID 不在有效范围', 'jobs.events', 422, field='Last-Event-ID') from exc
    async def stream():
        """把事件按 SSE 格式输出给前端。"""
        nonlocal cursor
        heartbeat = time.monotonic()
        while True:
            if await request.is_disconnected():
                break
            while cursor < len(job['events']):
                event = job['events'][cursor]
                cursor += 1
                yield f'id: {event["id"]}\nevent: {event["event"]}\ndata: {json.dumps(event["data"], ensure_ascii=False, separators=(",", ":"))}\n\n'
            if time.monotonic() - heartbeat >= 5:
                yield ': keepalive\n\n'
                heartbeat = time.monotonic()
            if job['status'] in ('done', 'failed', 'cancelled'):
                break
            await asyncio.sleep(0.1)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@router.post('/jobs/{job_id}/cancel')
async def cancel(job_id: str):
    """取消一个排队中 / 运行中的任务。"""
    job = jobs.get(job_id)
    async with jobs.admission:
        if job['status'] in ('queued', 'running'):
            jobs.tasks[job_id].cancel()
            await asyncio.gather(jobs.tasks[job_id], return_exceptions=True)
            await jobs.emit(job, 'error', dict(code='JOB_CANCELLED', message='任务已取消', where='jobs.cancel'),
                            status='cancelled')
    return dict(ok=True, status=job['status'])

@router.put('/projects/{project_id}/graph')
async def save_graph(project_id: str, body: dict):
    """前端保存画布（拖动位置等）：只存不生成，按契约 §3.6 校验结构。"""
    root = jobs.project_path(project_id)
    if not isinstance(body, dict):
        raise ProviderError('INVALID_REQUEST', 'graph 必须是 JSON 对象', 'projects.save', 422)
    if (body.get('project') or {}).get('id') != project_id:
        raise ProviderError('PROJECT_MISMATCH', 'graph.project.id 与路径中的项目不一致', 'projects.save', 409)
    # D1：客户端传来的 edges 直接丢弃，按 nodes[].choices 重算；不参与合并 / 比较 / 校验。
    body = normalize_graph(body)
    try:
        Graph.model_validate(body)                       # 字段类型（契约）
    except ValidationError as exc:
        first = exc.errors()[0]
        field = '.'.join(str(x) for x in first['loc']) or 'body'
        raise ProviderError('GRAPH_INVALID', f'graph 结构不符合契约：{field}: {first["msg"]}',
                            'projects.save', 422, field=field) from exc
    body.pop('chapters', None)  # GET 的章节视图由服务器从检查点生成。
    async with jobs.admission:
        jobs.require_idle(project_id)
        saved = await asyncio.to_thread(read_json, root / 'graph.json')
        state = saved.get('agent_state')
        if state is not None:
            node_ids = {node['id'] for node in body['nodes']}
            state['nodes'] = {nid: value for nid, value in state.get('nodes', {}).items()
                              if nid in node_ids}
            state.pop('job_id', None)
            body['agent_state'] = state
        # 语义不变量 I1–I8：必须在拼回 agent_state 之后判 —— 有没有 skeleton 决定严格还是宽松。
        body = gate_graph(body, 'api.save')
        await jobs.persist(atomic_json, root / 'graph.json', body)
    return dict(ok=True)


@router.post('/projects/{project_id}/nodes/{node_id}/regenerate')
async def regenerate_node(project_id: str, node_id: str, body: Regenerate):
    """只重新生成单个节点（素材或台词），不动整图。"""
    root = jobs.project_path(project_id)
    graph = await asyncio.to_thread(read_json, root / 'graph.json')
    if not any(n['id'] == node_id for n in graph['nodes']):
        raise ProviderError('NODE_NOT_FOUND', f'节点不存在: {node_id}', 'regenerate.start', 404)
    job = await jobs.submit(project_id, 'regenerate', dict(node_id=node_id, parts=body.parts))
    return dict(job_id=job['id'], sse=f'/api/jobs/{job["id"]}/events')


class NodeReviewIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['pass', 'fail']
    note: str = Field(default='', max_length=500)


@router.post('/projects/{project_id}/nodes/{node_id}/review')
async def review_node(project_id: str, node_id: str, body: NodeReviewIn):
    """逐节点审核：记下用户对这个节点的判定。

    只写 node.review，不动剧情本身 —— 驳回之后要不要重画由用户另外决定
    （重画走 /nodes/{id}/regenerate），两个动作分开，别把「标记」和「重写」搅在一起。
    """
    root = jobs.project_path(project_id)
    graph = await asyncio.to_thread(read_json, root / 'graph.json')
    target = next((n for n in graph['nodes'] if n['id'] == node_id), None)
    if target is None:
        raise ProviderError('NODE_NOT_FOUND', f'节点不存在: {node_id}', 'review.start', 404)
    target['review'] = dict(status=body.status, note=body.note, at=now())
    async with jobs.admission:
        await jobs.persist(atomic_json, root / 'graph.json', gate_graph(graph, 'api.review'))
    return dict(ok=True, node_id=node_id, review=target['review'])


class ReviewBatchIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['pass', 'fail'] = 'pass'
    note: str = Field(default='', max_length=500)


@router.post('/projects/{project_id}/nodes/review-batch')
async def review_batch(project_id: str, body: ReviewBatchIn):
    """把当前【还没审过】的节点一次性标记掉。

    几百个节点没法一个个点，所以必须给「其余全部通过」这条出路；
    已经审过的节点不动 —— 用户的判断不能被批量操作覆盖。
    """
    root = jobs.project_path(project_id)
    graph = await asyncio.to_thread(read_json, root / 'graph.json')
    stamp = now()
    count = 0
    for node in graph['nodes']:
        if (node.get('review') or {}).get('status'):
            continue
        node['review'] = dict(status=body.status, note=body.note, at=stamp)
        count += 1
    async with jobs.admission:
        await jobs.persist(atomic_json, root / 'graph.json', gate_graph(graph, 'api.review'))
    return dict(ok=True, updated=count)


@router.post('/projects/{project_id}/export')
async def export(project_id: str, body: Export):
    """导出工程包（renpy / unity 等格式）。"""
    root = jobs.project_path(project_id)
    if body.format == 'video':
        raise ProviderError('EXPORT_NOT_SUPPORTED', 'M2 支持 renpy/web，尚未实现录像', 'export.start', 501, field='format')
    graph = await asyncio.to_thread(read_json, root / 'graph.json')
    # 先补缺失素材再校验：降级路径（没配 key、art/voice 没跑）会留下
    # 「图里写着有、磁盘上没有」的素材，validate_graph 会先把导出拦死。
    filled = await asyncio.to_thread(ensure_assets, root, graph)
    try:
        await asyncio.to_thread(validate_graph, graph, root, False)
        if any(n['status'] != 'done' for n in graph['nodes']):
            raise ValueError('节点尚未生成完成')
    except (ValueError, OSError) as exc:
        raise ProviderError('GRAPH_NOT_READY', str(exc), 'export.validate', 409) from exc
    # 导出是纯本地写文件（秒级，Unity 除外）。先同步落盘再返回 out：
    # 前端拿到 out 后会立刻打开/校验文件，若只提交异步 job，返回瞬间目录还不存在。
    # job 仍会登记并发 SSE 事件（重复导出幂等，仅覆盖同名文件）。
    out_path = root / 'export' / body.format
    try:
        if body.format == 'unity':
            # 同一个 D:\UnityProject 同时只能跑一个构建 —— 串行化，否则两次构建互相踩。
            # 构建可能几十秒到十几分钟，asyncio.to_thread 不阻塞事件循环，但这次请求会等。
            async with UNITY_BUILD_LOCK:
                result = await asyncio.to_thread(
                    export_game, root, graph, body.format)
        else:
            result = await asyncio.to_thread(
                export_game, root, graph, body.format)
    except RuntimeError as exc:
        # Unity 构建失败：消息里已含 Unity 日志尾部，原样回给前端
        raise ProviderError('UNITY_BUILD_FAILED', str(exc), 'export.unity', 500) from exc
    except ValueError as exc:
        raise ProviderError('GRAPH_NOT_READY', str(exc), 'export.unity', 409) from exc
    except OSError as exc:
        raise ProviderError('STATE_NOT_WRITABLE',
                            f'{out_path}: {type(exc).__name__}: {exc}', 'export.write', 500) from exc
    # Unity 分支的产物不在 export/unity 下，而是 Unity 工程里的 exe —— 用真实路径回报
    if isinstance(result, dict) and result.get('out'):
        out_path = Path(result['out'])
    # 降级路径下有些素材图从来没生成过，导出时已用占位图补上 —— 要如实告诉用户哪几张是占位。
    filled = filled + take_missing_assets()
    warning = None
    if filled:
        shown = ', '.join(filled[:5]) + (f' 等 {len(filled)} 个' if len(filled) > 5 else '')
        warning = f'导出时有素材缺失，已用占位图补上：{shown}'
    job = await jobs.submit(project_id, 'export', body.model_dump(), warning=warning, exported=True)
    # ui/api.js 的 exportProject 用 `r.ok` 判成败、用 `r.out` 显示路径。
    return dict(ok=True, job_id=job['id'], out=str(out_path), pipeline=stage_report(graph),
                warnings=[f'素材缺失，已用占位图：{f}' for f in filled])

@router.post('/projects/{project_id}/ui/design')
async def design_ui(project_id: str, body: UiDesign):
    """按「文字要求 + 参考图」设计主界面 / 对话界面的风格。

    只生成一套新预设 + 渲染 HTML，**不动 graph.json、不动已导出的成品** ——
    设计失败不该把项目弄坏，这是它和「一键打包」分开走的原因。

    返回里的 `url` 是 `/api/assets/...` 下的静态 HTML，前端用 iframe 预览
    （跨源，必须带 sandbox，否则等于让项目里任意 HTML 在页面上跑脚本）。
    """
    root = jobs.project_path(project_id)
    if len(body.references) > ui_presets.MAX_REFERENCES:
        raise ProviderError(
            'TOO_MANY_REFERENCES',
            f'参考图有 {len(body.references)} 张，最多 {ui_presets.MAX_REFERENCES} 张',
            'ui.design', 400, field='references')
    graph = await asyncio.to_thread(read_json, root / 'graph.json')
    title = str((graph.get('project') or {}).get('title') or '奇迹工坊').strip()

    try:
        preset, preset_file = await asyncio.to_thread(
            ui_presets.run_design, requirement=body.requirement,
            references=list(body.references), base_id=body.base, title=title)
    except KeyError as exc:
        # base 指定了一个不存在的预设
        raise ProviderError('PRESET_NOT_FOUND', str(exc), 'ui.design', 400,
                            field='base') from exc
    except OSError as exc:
        raise ProviderError('STATE_NOT_WRITABLE',
                            f'{root}: {type(exc).__name__}: {exc}', 'ui.design', 500) from exc

    screens = ('title', 'dialog') if body.target == 'both' else (body.target,)
    rendered = []
    for screen in screens:
        html = ui_presets.render(preset, screen, title=title)
        # 必须落在 assets/ 下：预览走 /api/assets 读文件，而 safe_asset 只放行 assets/ 前缀
        # （它不是任意文件服务，是「项目资源」服务）。写成 ui/rendered/ 会 400。
        relative = f"assets/ui/rendered/{preset['id']}-{screen}.html"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, html, 'utf-8')
        rendered.append(dict(screen=screen, relative=relative,
                             url=f'/api/assets/{project_id}/{relative}',
                             bytes=len(html.encode('utf-8'))))

    design = dict(preset.get('design') or {})
    return dict(
        ok=True, mock=MOCK,
        preset=dict(id=preset['id'], name=preset['name'], vibe=preset['vibe'],
                    base=preset['base'], palette=dict(preset['palette']),
                    notes=preset['notes'], file=str(preset_file)),
        screens=rendered,
        analysis=dict(colors=list(design.get('colors') or []),
                      matched=list(design.get('matched') or []),
                      contrast=design.get('contrast'),
                      references_used=design.get('references_used', 0)),
        unsupported=list(design.get('unsupported') or []),
        note='界面已渲染；不满意就换要求或换参考图重新设计（不会改动成品与节点图）',
    )


@router.get('/assets/{project_id}/{asset_path:path}')
async def asset(project_id: str, asset_path: str):
    """按路径返回项目内的静态素材文件。"""
    root = jobs.project_path(project_id)
    try:
        path = safe_asset(root, asset_path)
    except ValueError as exc:
        raise ProviderError('INVALID_ASSET_PATH', str(exc), 'assets.get', 400) from exc
    # .html 是界面设计渲染出来的预览页（ui/rendered/*.html）。
    # 渲染器只输出静态 HTML，没有脚本；前端仍必须用 sandbox 的 iframe 加载。
    if not path.is_file() or path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.wav', '.html'):
        raise ProviderError('ASSET_NOT_FOUND', '资源不存在', 'assets.get', 404)
    return FileResponse(path)

