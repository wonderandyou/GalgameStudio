"""M2 mock 全链路：持久化任务、逐节点事件、标准 graph 和真实占位资产。"""
import asyncio
import copy
import io
import json
import os
import re
import struct
import time
import uuid
import wave
import zlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Request, Header
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field, ConfigDict
from .config import ROOT, MOCK
from .providers.base import ProviderError
from .store import atomic_json, atomic_bytes, read_json
from .engine.graph import validate_graph, edges_from_nodes, safe_asset
from .engine.export_mock import export_game

router = APIRouter(prefix='/api')

def now():
    return datetime.now(timezone.utc).isoformat()

class CreateProject(BaseModel):
    name: str = Field(min_length=1, max_length=120)

class Params(BaseModel):
    model_config = ConfigDict(extra='forbid')
    node_count: int = Field(default=8, ge=4, le=64)
    branch_depth: int = Field(default=2, ge=0, le=8)
    auto_voice: bool = True
    consistency: bool = True
    resolution: tuple[int, int] = (1920, 1080)

class Generate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    requirement: str = Field(min_length=1, max_length=10000)
    params: Params = Field(default_factory=Params)

class Export(BaseModel):
    format: Literal['renpy', 'web', 'video']

from .mock_data import draft, png, silent_wav

class Jobs:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.jobs = {}
        self.tasks = {}
        self.queue = asyncio.Lock()
        self.delay = min(2.0, max(0.0, float(os.getenv('GAL_MOCK_STEP_DELAY', '0.1'))))

    def project_path(self, project_id):
        if not re.fullmatch(r'p_[0-9a-f]{32}', project_id):
            raise ProviderError('PROJECT_NOT_FOUND', '项目不存在', 'projects.get', 404)
        path = self.root / 'projects' / project_id
        if not (path / 'project.json').is_file():
            raise ProviderError('PROJECT_NOT_FOUND', '项目不存在', 'projects.get', 404)
        return path

    def job_path(self, job_id):
        return self.root / '.jobs' / (job_id + '.json')

    def get(self, job_id):
        if job_id not in self.jobs:
            raise ProviderError('JOB_NOT_FOUND', '任务不存在', 'jobs.get', 404)
        return self.jobs[job_id]

    async def save(self, job):
        await asyncio.to_thread(atomic_json, self.job_path(job['id']), job)

    async def emit(self, job, event, data):
        job['events'].append(dict(id=len(job['events']) + 1, event=event, data=copy.deepcopy(data)))
        await self.save(job)
        if event in ('log', 'error'):
            path = self.project_path(job['project_id']) / 'logs' / 'run.log'
            text = data.get('text', data.get('message', ''))
            level = data.get('level', 'error')
            def append():
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open('a', encoding='utf-8') as stream:
                        stream.write(f'[{now()}] [{level}] [mock] {text}\n')
                except OSError as exc:
                    raise ProviderError('STATE_NOT_WRITABLE', f'{path}: {type(exc).__name__}: {exc}', 'jobs.log', 500) from exc
            await asyncio.to_thread(append)

    async def agent(self, job, agent, state, progress, msg):
        await self.emit(job, 'agent', dict(agent=agent, state=state, progress=progress, msg=msg))

    async def submit(self, project_id, kind, body):
        self.project_path(project_id)
        if any(j['project_id'] == project_id and j['status'] in ('queued', 'running') for j in self.jobs.values()):
            raise ProviderError('PROJECT_BUSY', '项目已有运行任务', 'jobs.submit', 409)
        job_id = 'j_' + uuid.uuid4().hex
        job = dict(id=job_id, project_id=project_id, kind=kind, body=body, status='queued', events=[], stages=[], started=now())
        await self.save(job)
        self.jobs[job_id] = job
        self.tasks[job_id] = asyncio.create_task(self.run(job))
        return job

    async def run(self, job):
        async with self.queue:
            try:
                job['status'] = 'running'
                await self.save(job)
                if job['kind'] == 'generate':
                    await self.generate(job)
                else:
                    await self.export(job)
                job['status'] = 'done'
                await self.save(job)
            except asyncio.CancelledError:
                # 关闭服务时保留 running，下一次启动恢复；显式取消由接口写终态。
                raise
            except Exception as exc:
                error = exc.payload if isinstance(exc, ProviderError) else dict(code='JOB_FAILED', message=f'{type(exc).__name__}: {exc}', where='mock.' + job['kind'])
                # 磁盘故障也必须让在线 SSE 收到终态，不能无限等待。
                try:
                    await self.agent(job, job.get('active_agent', 'build'), 'failed', 0.0, error['message'])
                    await self.emit(job, 'error', error)
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
        started = time.monotonic()
        root = self.project_path(job['project_id'])
        params = job['body']['params']
        if not job['stages']:
            project = await asyncio.to_thread(read_json, root / 'project.json')
            graph = draft(project, job['body'])
            await asyncio.to_thread(atomic_json, root / 'graph.json', graph)
        else:
            graph = await asyncio.to_thread(read_json, root / 'graph.json')
        await self.emit(job, 'log', dict(level='info', text='GAL_MOCK=1：不调用模型；纯色 PNG 与一秒静音 WAV，仅用于协议联调。'))
        for agent in ('writer', 'art', 'voice', 'build'):
            if agent in job['stages']:
                await self.emit(job, 'log', dict(level='info', text=f'断点恢复：跳过已完成阶段 {agent}'))
                continue
            job['active_agent'] = agent
            await self.agent(job, agent, 'run', 0.0, '开始模拟阶段')
            if agent == 'art':
                for relative, width, height, color in [('assets/bg/mock.png', 64, 36, (35, 50, 75, 255)), ('assets/char/mock.png', 32, 64, (125, 86, 155, 255)), ('assets/ref/mock.png', 32, 64, (125, 86, 155, 255))]:
                    await asyncio.to_thread(atomic_bytes, root / relative, png(width, height, color))
            if agent == 'voice' and params['auto_voice']:
                await asyncio.to_thread(atomic_bytes, root / 'assets/voice/mock.wav', silent_wav())
            for i, node in enumerate(graph['nodes']):
                await asyncio.sleep(self.delay)
                if agent == 'writer':
                    node['status'] = 'generating'
                    patch = copy.deepcopy(node)
                elif agent == 'art':
                    patch = dict(bg=node['bg'], chars=node['chars'])
                elif agent == 'voice':
                    node['lines'][0]['voice'] = 'assets/voice/mock.wav' if params['auto_voice'] else None
                    node['lines'][0]['dur'] = 1.0 if params['auto_voice'] else 4 / 4.5
                    patch = dict(lines=node['lines'])
                else:
                    node['status'] = 'done'
                    patch = dict(status='done')
                await asyncio.to_thread(atomic_json, root / 'graph.json', graph)
                await self.emit(job, 'node', dict(node_id=node['id'], patch=patch))
                await self.agent(job, agent, 'run', (i + 1) / len(graph['nodes']), f'节点 {i+1}/{len(graph["nodes"])}')
            if agent == 'build':
                await asyncio.to_thread(validate_graph, graph, root, params['auto_voice'])
            job['stages'].append(agent)
            await self.agent(job, agent, 'done', 1.0, '阶段完成')
        job['status'] = 'done'
        await self.emit(job, 'done', dict(stats=dict(nodes=len(graph['nodes']), edges=len(graph['edges']), images=3, voices=len(graph['nodes']) if params['auto_voice'] else 0, cost=0.0, elapsed_s=round(time.monotonic()-started, 3))))

    async def export(self, job):
        root = self.project_path(job['project_id'])
        graph = await asyncio.to_thread(read_json, root / 'graph.json')
        await asyncio.to_thread(validate_graph, graph, root, False)
        job['active_agent'] = 'build'
        await self.agent(job, 'build', 'run', 0.0, '开始导出占位素材工程')
        try:
            await asyncio.to_thread(export_game, root, graph, job['body']['format'])
        except OSError as exc:
            raise ProviderError('STATE_NOT_WRITABLE', f'{root / "export"}: {type(exc).__name__}: {exc}', 'export.write', 500) from exc
        await self.agent(job, 'build', 'done', 1.0, '导出完成')
        job['status'] = 'done'
        await self.emit(job, 'done', dict(stats=dict(nodes=len(graph['nodes']), edges=len(graph['edges']), images=3, voices=sum(bool(l['voice']) for n in graph['nodes'] for l in n['lines']), cost=0.0, elapsed_s=0.0)))

    async def restore(self):
        for path in (self.root / '.jobs').glob('j_*.json'):
            job = await asyncio.to_thread(read_json, path)
            self.jobs[job['id']] = job
            if job['status'] in ('queued', 'running'):
                if MOCK:
                    self.tasks[job['id']] = asyncio.create_task(self.run(job))
                else:
                    job['status'] = 'failed'
                    await self.emit(job, 'error', dict(code='MOCK_DISABLED', message='恢复 mock 任务需要 GAL_MOCK=1', where='jobs.restore'))

jobs = Jobs()

@asynccontextmanager
async def lifespan(app):
    await jobs.restore()
    yield
    for task in jobs.tasks.values():
        task.cancel()
    await asyncio.gather(*jobs.tasks.values(), return_exceptions=True)

@router.post('/projects')
async def create_project(body: CreateProject):
    project_id = 'p_' + uuid.uuid4().hex
    root = jobs.root / 'projects' / project_id
    project = dict(id=project_id, name=body.name, resolution=[1920, 1080], created_at=now())
    await asyncio.to_thread(atomic_json, root / 'graph.json', dict(version=1, project=project, characters=[], nodes=[], edges=[]))
    await asyncio.to_thread(atomic_json, root / 'project.json', project)
    return dict(id=project_id, path=str(root))

@router.get('/projects')
async def list_projects():
    return [await asyncio.to_thread(read_json, p) for p in (jobs.root / 'projects').glob('p_*/project.json')]

@router.get('/projects/{project_id}/graph')
async def get_graph(project_id: str):
    root = jobs.project_path(project_id)
    return await asyncio.to_thread(read_json, root / 'graph.json')

@router.post('/projects/{project_id}/generate')
async def generate(project_id: str, body: Generate):
    if not MOCK:
        raise ProviderError('REAL_GENERATION_NOT_IMPLEMENTED', 'M2 只支持 GAL_MOCK=1；未调用任何模型', 'generate.start', 501)
    if any(not 64 <= x <= 8192 for x in body.params.resolution):
        raise ProviderError('INVALID_REQUEST', 'resolution 每边须为 64–8192', 'generate.validation', 422, field='params.resolution')
    job = await jobs.submit(project_id, 'generate', body.model_dump(mode='json'))
    return dict(job_id=job['id'], sse=f'/api/jobs/{job["id"]}/events')

@router.get('/jobs/{job_id}/events')
async def events(job_id: str, request: Request, last_event_id: str | None = Header(default=None)):
    job = jobs.get(job_id)
    try:
        cursor = int(last_event_id or '0')
        if cursor < 0 or cursor > len(job['events']):
            raise ValueError()
    except ValueError as exc:
        raise ProviderError('INVALID_EVENT_ID', 'Last-Event-ID 不在有效范围', 'jobs.events', 422, field='Last-Event-ID') from exc
    async def stream():
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
    job = jobs.get(job_id)
    if job['status'] in ('queued', 'running'):
        jobs.tasks[job_id].cancel()
        await asyncio.gather(jobs.tasks[job_id], return_exceptions=True)
        job['status'] = 'cancelled'
        await jobs.emit(job, 'error', dict(code='JOB_CANCELLED', message='任务已取消', where='jobs.cancel'))
    return dict(ok=True, status=job['status'])

@router.post('/projects/{project_id}/export')
async def export(project_id: str, body: Export):
    root = jobs.project_path(project_id)
    if body.format == 'video':
        raise ProviderError('EXPORT_NOT_SUPPORTED', 'M2 支持 renpy/web，尚未实现录像', 'export.start', 501, field='format')
    graph = await asyncio.to_thread(read_json, root / 'graph.json')
    try:
        await asyncio.to_thread(validate_graph, graph, root, False)
        if any(n['status'] != 'done' for n in graph['nodes']):
            raise ValueError('节点尚未生成完成')
    except (ValueError, OSError) as exc:
        raise ProviderError('GRAPH_NOT_READY', str(exc), 'export.validate', 409) from exc
    job = await jobs.submit(project_id, 'export', body.model_dump())
    return dict(job_id=job['id'], out=str(root / 'export' / body.format))

@router.get('/assets/{project_id}/{asset_path:path}')
async def asset(project_id: str, asset_path: str):
    root = jobs.project_path(project_id)
    try:
        path = safe_asset(root, asset_path)
    except ValueError as exc:
        raise ProviderError('INVALID_ASSET_PATH', str(exc), 'assets.get', 400) from exc
    if not path.is_file() or path.suffix.lower() not in ('.png', '.wav'):
        raise ProviderError('ASSET_NOT_FOUND', '资源不存在', 'assets.get', 404)
    return FileResponse(path)

