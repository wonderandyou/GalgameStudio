"""编排编剧、美术、配音和装配，共用同一张可断点恢复的剧情图。"""
import asyncio
import copy
import time

from ..agents.loop import DIRECTOR_ROUNDS, direct_production, review_art, run_writer
from ..agents.tools import AgentTools
from ..asset_records import read_records
from ..mock_data import png, silent_wav
from ..providers.base import ProviderError
from .contracts import (
    MOCK_IMPACTS, OFF_IMPACTS, STAGE_CONTRACTS, check_art, check_voice, check_writer, stage_report,
)
from .graph import validate_graph

silent_wav_for = silent_wav


def _declares(tool_name, param, agent='art'):
    """工具 schema 里有没有声明这个参数。

    立绘的 transparent 是另一个子智能体给 generate_image 加的（他要改实现，
    参数得先进 schema 才过得了参数校验）。这里运行时探测一次：
    声明了就把 transparent=True 传下去，没声明就先不传 —— 谁先落地都不会把
    整条链路卡死。
    """
    from ..agents import tool_schema
    for spec in tool_schema.TOOLS.get(agent, []):
        function = spec.get('function') or {}
        if function.get('name') == tool_name:
            return param in ((function.get('parameters') or {}).get('properties') or {})
    return False

# 没定风格圣经时的中性兜底。
# 有 LLM 时 agent 会调 set_style 定制；降级时靠这里保证出图仍守一条线，
# 而不是「没模型就随便画」。
DEFAULT_STYLE = dict(
    palette=['#1f2430', '#2b3245', '#c4564b', '#e8e6e3'],
    medium='赛璐璐，细描线，色块干净',
    light='中性色调，中对比，柔和顶光',
    typeface='标题 思源宋体 Bold；正文 思源黑体 Regular；UI 思源黑体 Medium',
    negative=['文字', '水印', '签名', '过曝'],
    refs=[],
    version=1,
)


class Pipeline:
    """为一项生成任务驱动工具并发布可重放的进度事件。"""

    def __init__(self, graph, body, root, providers, emit, save_graph, save_asset,
                 delay=0.0, emit_many=None, mock=False, degraded=None):
        """装配一个生成流水线。

        参数
        ----
        graph: 项目的完整图（含 agent_state），所有阶段都读写同一份。
        body: 这次生成的需求描述（用户填的设定）。
        root: 项目根目录，素材写在这里。
        providers: {角色: provider} 或 None；None 表示整体走 mock。
        emit: 单事件回调 `async (type, payload)`，直接喂 SSE。
        emit_many: 批量事件回调；不给就退化成逐个 emit。
        save_graph / save_asset: 落盘回调，注入进来是为了让测试能换成内存实现。
        delay: 每步之间的人为延迟（仅 debug 用，让前端动画看得清）。
        mock: 是否处于 mock 模式（决定降级时的日志措辞）。
        degraded: 预先算好的降级原因，一般是断点续跑时从上次状态带回的。
        """
        self.graph = graph
        self.body = body
        self.params = body['params']
        self.root = root
        self.providers = {} if mock else dict(providers or {})
        self.emit = emit
        self.emit_many = emit_many
        self.save_graph = save_graph
        self.save_asset = save_asset
        self.delay = delay
        self.mock = mock
        self.unavailable = degraded or {}
        self.start = time.monotonic()
        self.tools = AgentTools(graph, body, root, providers=self.providers,
                                save_graph=save_graph, save_asset=save_asset)

    @property
    def state(self):
        """流水线的持久化状态块（`agent_state.pipeline`），断点续跑就看它。"""
        return self.graph['agent_state'].setdefault('pipeline', {})

    @property
    def stages(self):
        """每个阶段最后用的是 real 还是 mock；前端右侧那排「4/6 就绪」读的就是它。"""
        return self.state.setdefault('stages', {})

    @property
    def degraded(self):
        """{阶段: 降级原因}。只记第一次的原因 —— 后面的失败多半是第一因的连锁。"""
        return self.state.setdefault('degraded', {})

    @property
    def impacts(self):
        return self.state.setdefault('impacts', {})

    async def log(self, text, level='info'):
        """推一条人看的日志到前端 run.log。level 是 info / warn。"""
        await self.emit('log', dict(level=level, text=text))

    async def checkpoint(self, phase, chapter=None, note=''):
        """落一份续写快照 + 记一条运行日志。

        失败只记日志、不往上抛：快照是保险，保险没买上不该把整次生成拖垮。
        """
        try:
            return self.tools.checkpoints.note(
                self.graph, job=self.state.get('job_id'), agent=None,
                phase=phase, chapter=chapter, note=note)
        except Exception as exc:                    # noqa: BLE001
            await self.log(f'检查点写入失败：{type(exc).__name__}: {exc}', 'warn')
            return None

    async def agent(self, name, state, progress, msg, **details):
        """推一条「某个 agent 干到哪了」的进度事件，前端那六格状态读它。

        progress 用 0~1；state 是 run / done / failed / skip。
        details 里常带 extra 字段（比如 writer 的 chapter_id），前端按需取。
        """
        await self.emit('agent', dict(agent=name, state=state, progress=progress, msg=msg, **details))

    async def node(self, node, patch, *, persist=True):
        """推一个节点的增量更新（patch），前端据此就地刷新那一张卡片。

        persist=True 时先把整图落盘再发事件：这样前端刷新页面拿到的
        一定是已经写进去的内容，不会出现「界面改了、文件没改」的错觉。
        """
        if persist:
            await self.save_graph(self.graph)
        await self.emit('node', dict(node_id=node['id'], patch=copy.deepcopy(patch)))

    async def publish_nodes(self, nodes, fields=None):
        """把一批节点分小批推给前端。

        为什么分批：SSE 每条记录都要落一份日志，一次性推五百个节点会把
        写盘次数顶上去，前端也会被一屏截图级的更新卡住。32 个一批实测够快。
        """
        # 按小批次持久化 SSE 记录，避免每个事件都重写一份完整日志。
        for start in range(0, len(nodes), 32):
            batch = nodes[start:start + 32]
            if self.delay:
                await asyncio.sleep(self.delay * len(batch))
            patches = [(node, node if fields is None else {key: node[key] for key in fields})
                       for node in batch]
            if self.emit_many is None:
                for node, patch in patches:
                    await self.node(node, patch, persist=False)
            else:
                await self.emit_many([('node', dict(node_id=node['id'], patch=patch))
                                      for node, patch in patches])

    async def degrade(self, stage, reason, *, impact=''):
        """保留首个原因，并记录所有不同的下游影响。"""
        self.stages[stage] = 'mock'
        if stage == 'writer' and self.providers.get('writer') is None:
            self.state['writer_provider'] = 'mock'
            impact = impact or '未完成的剧本使用内置文案'
        previous = self.impacts.get(stage)
        if impact:
            if previous and previous != MOCK_IMPACTS.get(stage):
                if impact not in previous.split(' / '):
                    self.impacts[stage] = f'{previous} / {impact}'
            else:
                self.impacts[stage] = impact
        else:
            self.impacts.setdefault(stage, MOCK_IMPACTS.get(stage, ''))
        if stage not in self.degraded:
            self.degraded[stage] = reason
            await self.log(f'{stage} 使用兜底产出：{reason}', 'info' if self.mock else 'warn')

    async def tool(self, role, name, args):
        """调一个 agent 工具，**要求必须成功**：失败直接抛 ProviderError。

        用于流程中「这一步不做后面就没意义」的动作（比如装配前的校验）。
        允许失败的那些走 `material()`。
        """
        result = await self.tools.dispatch(role, name, args)
        if not result['ok']:
            raise ProviderError(result['error'], result['message'], f'{role}.{name}')
        return result

    async def material(self, role, name, args):
        """供应商链耗尽后补占位。参考图错误只影响当前图。"""
        result = await self.tools.dispatch(role, name, args)
        provider = self.tools.providers.get(role)
        if not result['ok'] and provider is not None:
            reference_error = result['error'].startswith('REFERENCE_')
            await self.degrade(role, result['message'])
            self.tools.providers[role] = None
            try:
                retry_args = {key: value for key, value in args.items() if key != 'force'} if reference_error else args
                result = await self.tools.dispatch(role, name, retry_args)
            finally:
                if reference_error:
                    self.tools.providers[role] = provider
        if not result['ok']:
            raise ProviderError(result['error'], result['message'], f'{role}.{name}')
        return result

    async def ensure_characters_from_references(self):
        """把「带名字/性别的角色参考图」预建成角色。

        参考图以前只是素材，角色是 writer 自己编的 —— 用户明明传了「女主 林夏」，
        agent 照样叫她「她」。这条把两者接上：writer 一开局 list_characters 就能看到
        「林夏 / female / 这张参考图 / 这个音色」。
        """
        import hashlib
        import re as _re
        from ..contract import Character

        items = await asyncio.to_thread(read_records, self.root, 'refs.json')
        wanted = [item for item in items
                  if item.get('kind') == 'character'
                  and (item.get('name') or item.get('character_id'))
                  # ★ detached：用户把挂在这张参考图上的角色删了。
                  #   图和名字都还留着，但不能再按它自动重建角色 ——
                  #   否则「删掉的角色下一次生成又冒出来」。
                  and not item.get('detached')]
        if not wanted:
            return
        characters = {c['id']: c for c in self.graph['characters']}
        added = []
        for item in wanted:
            # 有名字就按**名字**生成稳定 id：同一个角色的两张参考图（正脸+侧脸）
            # 才会落到同一个角色上；没名字才退回按图片哈希。
            stem = (item.get('name') or '').strip()
            seed = stem or str(item.get('sha256') or item['id'])
            cid = item.get('character_id') or ('ch_' + hashlib.sha256(seed.encode('utf-8')).hexdigest()[:8])
            if not _re.fullmatch(r'[a-z][a-z0-9_]*', cid):
                continue
            old = characters.get(cid, {})
            if not old and len(characters) >= 8:
                continue
            gender = item.get('gender')
            if gender not in ('male', 'female', 'other'):
                gender = old.get('gender') or 'other'
            entry = dict(
                id=cid,
                name=(item.get('name') or old.get('name') or cid)[:40],
                color=old.get('color') or '#d946ef',
                ref_image=item['asset'],
                voice_id=old.get('voice_id') or '',
                look=old.get('look') or '',
                gender=gender,
            )
            try:
                characters[cid] = Character.model_validate(entry).model_dump(mode='json')
            except Exception as exc:              # noqa: BLE001
                await self.log(f'参考图 {item["id"][:12]} 预建角色失败：{exc}', 'warn')
                continue
            added.append(cid)
        if not added:
            return
        self.graph['characters'] = list(characters.values())
        await self.save_graph(self.graph)
        await self.log('从参考图预建角色：' + '、'.join(
            f'{characters[cid]["name"]}({characters[cid]["gender"]})' for cid in added))

    async def writer(self):
        """确定编剧模式与供应商，骨架续写缺少模型时仍可补齐对白。"""
        mode = self.params.get('writer_mode', 'skeleton')
        await self.agent('writer', 'run', 0.0, '准备章节结构' if mode == 'legacy' else '准备整图骨架')
        prior_mode = self.state.get('writer_provider', self.stages.get('writer'))
        if mode == 'legacy' and prior_mode == 'real' and self.providers.get('writer') is None:
            raise ProviderError('WRITER_UNAVAILABLE', '恢复写作需要可用的文本 provider', 'pipeline.writer')
        if prior_mode == 'mock':
            self.providers['writer'] = self.tools.providers['writer'] = None
        if self.providers.get('writer') is None:
            reason = self.unavailable.get('writer', '没有可用的文本 provider')
            await self.degrade('writer', reason)
        else:
            self.state['writer_provider'] = 'real'
            self.stages.setdefault('writer', 'real')
        await self.save_graph(self.graph)
        self.state['writer'] = await run_writer(self, self.tools)

    async def ensure_style(self):
        """没定风格圣经时用中性默认兜底，保证降级时出图仍守同一条线。"""
        if self.graph.get('style'):
            return self.graph['style']
        style = dict(DEFAULT_STYLE)
        self.graph['style'] = style
        await self.save_graph(self.graph)
        await self.log('未指定风格圣经，用中性默认；建议先 set_style 定制')
        return style

    async def ensure_production_plan(self):
        """美术阶段开工前必须有排产表。

        writer 阶段结束时编剧已经排过一张；断点恢复的老项目（或上一版跑完
        还没排产的任务）缺这张表，这里现场补 —— 有模型就让导演排，没模型按
        剧本字段兜底，不会因为「缺表」把整轮卡住。
        """
        plan = self.graph['agent_state'].get('production_plan')
        if plan and plan.get('version'):
            await self.log(f'美术按编剧排产表开工（{plan.get("source")}）：'
                           f'{len(plan.get("backgrounds") or [])} 个背景 · '
                           f'{len(plan.get("poses") or [])} 个立绘')
            return plan
        await self.log('还没有编剧排产表，现场让导演补一张', 'info')
        return await direct_production(self, self.tools)

    @staticmethod
    def _plan_key(key):
        """执行清单的 key → 排产表里对应的 key。

        背景是 1:1，原样返回；立绘差分的 key 是 `char:<id>:<stance>:<mood>`，
        而排产表里每个角色只有**一条**（导演只写一次外形锚点，不用按姿态拆），
        所以截回角色 id 去查表 —— 不截的话差分条目永远查不到 brief/priority，
        会全部被排到最后。
        """
        text = str(key or '')
        if text.startswith('char:'):
            parts = text.split(':')
            if len(parts) >= 2 and parts[1]:
                return parts[1]
        return text

    @staticmethod
    def _plan_brief(plan, kind, key):
        """从排产表里取某条目的 brief；没有就返回空串（调用方用兜底文案）。"""
        lookup = Pipeline._plan_key(key)
        for item in plan.get(kind) or []:
            if item.get('key') == lookup:
                return (item.get('brief') or '').strip()
        return ''

    @staticmethod
    def _plan_label(plan, kind, key):
        """编剧给这张图起的中文短名（会变成文件名）。"""
        lookup = Pipeline._plan_key(key)
        for item in plan.get(kind) or []:
            if item.get('key') == lookup:
                return (item.get('label') or '').strip()
        return ''

    @staticmethod
    def _plan_order(plan, kind, pending, key_of):
        """按导演的 priority 排先后。计划里没有的新素材排最后，不会漏画。"""
        rank = {}
        for item in plan.get(kind) or []:
            key = item.get('key')
            try:
                rank[key] = int(item.get('priority') or 0)
            except (TypeError, ValueError):
                rank[key] = 0

        def order(item):
            key = Pipeline._plan_key(key_of(item))
            if key not in rank:
                return (1, 0, key)
            return (0, rank[key] if rank[key] > 0 else 999, key)

        return sorted(pending, key=order)

    def _image_args(self, plan, kind, key, target, prompt, negative, *, force=False, extra=None):
        """拼一次生图的参数。

        `force` 用于「用户点了重新生成」：没有它，generate_image 会因为
        目标文件已存在而直接复用旧图 —— 那正是重生成按钮点了没反应的原因。
        `extra` 放各供应商特有的字段（尺寸、参考图列表之类）。
        """
        args = dict(target=target, key=key, prompt=prompt, negative=negative)
        if force:
            args['force'] = True
        if extra:
            args.update(extra)
        # 编剧给的中文短名 → 图的名字（01-雨夜天台.png），人一眼能认出是哪张。
        label = self._plan_label(plan, kind, key)
        if label and _declares('generate_image', 'label'):
            args['label'] = label
        return args

    async def _art_batch(self, plan, kind, entries, target, draft_target, label, extra=None):
        """按编剧的计划画一批素材，画完交回导演验收；他说重做就带新描述重画。

        `extra` 是给 generate_image 的附加参数（立绘的 transparent）。
        """
        if not entries:
            await self.log(f'{label}：没有待画的素材，跳过')
            return dict(total=0, generated=0, reused=0, failed=[], rounds=0, keys=[])
        outcomes, failures = [], {}
        await self.agent('art', 'run', 0.0, f'{label} 0/{len(entries)}（按编剧排产顺序）')
        for index, item in enumerate(entries, 1):
            key = item['key']
            brief = (self._plan_brief(plan, kind, key) or item.get('prompt')
                     or item.get('name') or key)
            try:
                # 画面描述由编剧定，画法/光影/色板/负面词仍由 draft_prompt 按风格圣经补。
                draft = await self.tool('art', 'draft_prompt', dict(
                    target=draft_target, key=key, brief=brief))
                args = self._image_args(plan, kind, key, target, draft['prompt'],
                                        draft['negative'], extra=extra)
                result = await self.material('art', 'generate_image', args)
                failures.pop(key, None)
                outcomes = [row for row in outcomes if row['key'] != key]
                outcomes.append(dict(key=key, prompt=draft['prompt'],
                                     asset=result.get('asset'),
                                     generated=bool(result.get('generated')),
                                     reused=result.get('reused'),
                                     node_count=len(result.get('node_ids') or [])))
            except ProviderError as exc:
                failures[key] = dict(key=key, error=exc.payload['code'],
                                     message=(exc.payload.get('message') or '')[:160])
                await self.log(f'{label} · {key} 出图失败：{exc.payload["code"]} '
                               f'{(exc.payload.get("message") or "")[:80]}', 'warn')
            await self.agent('art', 'run', index / len(entries), f'{label} {index}/{len(entries)}')

        rounds, history = 0, []
        # 只有真 provider 才值得让导演验收：降级成占位图时验收没有意义。
        if self.tools.providers.get('art') is not None:
            for _ in range(DIRECTOR_ROUNDS):
                review = await review_art(self, kind, plan, outcomes, list(failures.values()), history)
                redo = [item for item in review['verdicts'] if item['action'] == 'redo']
                skipped = [item for item in review['verdicts'] if item['action'] == 'skip']
                if skipped:
                    await self.log(f'导演跳过了 {len(skipped)} 张{label}：' +
                                   '、'.join(item['key'] for item in skipped), 'warn')
                if not redo:
                    break
                rounds += 1
                history.append([{'key': item['key'], 'brief': item['brief']} for item in redo])
                await self.log(f'导演要求重画 {len(redo)} 张{label}（第 {rounds} 轮）：' +
                               '；'.join(f'{item["key"]}←{item["reason"] or item["brief"][:40]}'
                                        for item in redo))
                for position, verdict in enumerate(redo, 1):
                    key = verdict['key']
                    brief = verdict['brief'] or self._plan_brief(plan, kind, key)
                    try:
                        draft = await self.tool('art', 'draft_prompt', dict(
                            target=draft_target, key=key, brief=brief))
                        args = self._image_args(plan, kind, key, target, draft['prompt'],
                                                draft['negative'], force=True, extra=extra)
                        result = await self.material('art', 'generate_image', args)
                        failures.pop(key, None)
                        outcomes = [row for row in outcomes if row['key'] != key]
                        outcomes.append(dict(key=key, prompt=draft['prompt'],
                                             asset=result.get('asset'),
                                             generated=bool(result.get('generated')),
                                             reused=result.get('reused'),
                                             node_count=len(result.get('node_ids') or [])))
                    except ProviderError as exc:
                        failures[key] = dict(key=key, error=exc.payload['code'],
                                             message=(exc.payload.get('message') or '')[:160])
                        await self.log(f'{label} · {key} 重画仍失败：{exc.payload["code"]}', 'warn')
                    await self.agent('art', 'run',
                                     (len(outcomes) + len(failures)) / max(1, len(entries)),
                                     f'{label} 重画 {position}/{len(redo)}')

        report = dict(total=len(entries),
                      generated=sum(1 for row in outcomes if row.get('generated')),
                      reused=sum(1 for row in outcomes if row.get('reused')),
                      failed=[row['key'] for row in failures.values()],
                      rounds=rounds, keys=[row['key'] for row in outcomes])
        if report['failed']:
            await self.log(f'{label} 结束：成功 {len(outcomes)} 张，失败 {len(report["failed"])} 张'
                           f'（{", ".join(report["failed"])}），将在美术关卡检查素材是否齐全', 'warn')
        return report

    async def art_backgrounds(self, plan):
        """背景产线：按编剧给的画面描述和优先级画，进度、失败各自结算。"""
        pending = await self.tool('art', 'list_pending_assets', {})
        entries = self._plan_order(plan, 'backgrounds', pending['backgrounds'],
                                   lambda item: item['key'])
        return await self._art_batch(plan, 'backgrounds', entries, 'background', 'background', '背景')

    async def art_poses(self, plan):
        """立绘产线：先基底、再差分，都要透明底。

        顺序不能反：差分是拿基底做图生图的（同一个角色才不换脸），
        基底没出时 generate_image 会直接拒绝，先画差分等于白画。
        两条都是立绘，进度和失败各自结算，一份挂了不拖另一份。
        """
        pending = await self.tool('art', 'list_pending_assets', {})
        bases_by_id = {item['key']: item for item in pending.get('bases') or []}
        real_art = self.tools.providers.get('art') is not None
        for char in self.graph['characters']:
            base = char['ref_image']
            if not self.tools._asset_exists(base) or (real_art and self.tools._is_placeholder(base)):
                bases_by_id.setdefault(char['id'], dict(key=char['id'], name=char['name'],
                                                       prompt=char.get('look') or char['name']))
        bases = self._plan_order(plan, 'poses', bases_by_id.values(), lambda item: item['key'])
        extra = dict(transparent=True) if _declares('generate_image', 'transparent') else None
        base_report = await self._art_batch(
            plan, 'poses', bases, 'pose', 'character', '立绘·基底', extra)
        if bases:
            pending = await self.tool('art', 'list_pending_assets', {})
        poses = self._plan_order(plan, 'poses', pending.get('poses') or [], lambda item: item['key'])
        pose_report = await self._art_batch(
            plan, 'poses', poses, 'pose', 'character', '立绘·差分', extra)
        if not bases:
            return pose_report
        if not poses:
            return base_report
        # 两份报告并成一份：art() 只收一个 dict，字段沿用原来的
        return dict(
            total=int(base_report.get('total') or 0) + int(pose_report.get('total') or 0),
            generated=int(base_report.get('generated') or 0) + int(pose_report.get('generated') or 0),
            reused=int(base_report.get('reused') or 0) + int(pose_report.get('reused') or 0),
            failed=list(base_report.get('failed') or []) + list(pose_report.get('failed') or []),
            rounds=max(int(base_report.get('rounds') or 0), int(pose_report.get('rounds') or 0)),
            keys=list(base_report.get('keys') or []) + list(pose_report.get('keys') or []),
        )

    async def art(self):
        """美术阶段：背景、立绘两条独立产线，都照编剧的排产表干活。

        以前这里是一把 list_pending_assets 全画掉，画完不管好坏。现在：
        先读导演的排产表（画面描述 / 优先级），两条产线各自跑、各自报进度、
        各自结算失败，画完由导演验收、不合格带着新描述重画。
        """
        await self.agent('art', 'run', 0.0, '按编剧排产表开工')
        if not self.params.get('auto_art', True):
            # ★ 用户明确关掉美术：只跑编剧，图全用占位。
            #   必须在这里先落三张占位（bg / char / ref），否则骨架里引用的
            #   assets/bg/mock.png 等不存在，validate_graph 会把整轮判死。
            await self.log('美术阶段已关闭（auto_art=false）→ 全部使用占位图，不调用任何生图接口',
                           level='info')
            # ★ 用户**主动关掉**美术，不算降级：标 'off'，绝不能进 `degraded`。
            #   以前这里调的是 degrade()，而任务收尾时 `mock=bool(self.degraded)` ——
            #   于是真实模式跑出来的任务在 done 事件里 mock=True，
            #   用户以为这次没用真模型、没花钱（第三轮验证的 P2-D）。
            self.stages['art'] = 'off'
            self.impacts['art'] = OFF_IMPACTS['art']
            for rel, data in (('assets/bg/mock.png', png(64, 36, (35, 50, 75, 255))),
                              ('assets/char/mock.png', png(32, 64, (125, 86, 155, 255))),
                              ('assets/ref/mock.png', png(32, 64, (125, 86, 155, 255)))):
                await self.save_asset(rel, data)
            await self.ensure_style()
            self.state['art'] = dict(backgrounds=dict(skipped=True), poses=dict(skipped=True))
            return
        if self.providers.get('art') is None:
            reason = self.unavailable.get('art', '没有可用的图像 provider')
            # 参考图是「加分项」，不是「必需项」。
            # 以前这里有参考图就直接抛错，把整轮生成搞挂（踩过：用户传了参考图但
            # provider 没启用，48 个节点白写，只留一条 REFERENCE_NOT_SUPPORTED）。
            # 正确做法是警告 + 降级成不用参考图继续跑，别让用户整轮白等。
            if not self.mock and await asyncio.to_thread(read_records, self.root, 'refs.json'):
                await self.log(
                    f'参考图这次用不上（{reason}）→ 本次改为不用参考图的普通生成。'
                    f'想让参考图生效，去「接口配置」确认图像 provider 已启用并保存。',
                    level='warn')
            await self.degrade('art', reason)
        if self.mock:
            # 骨架图引用的占位必须先落盘：validate_graph 会因为「引用了不存在的资产」
            # 把整轮生成判死。以前这里只建了 bg，角色立绘和角色参考图两个占位是漏的
            # （骨架里节点写 char/mock.png、角色表写 ref/mock.png，两个都没人生成）。
            await self.save_asset('assets/bg/mock.png', png(64, 36, (35, 50, 75, 255)))
            await self.save_asset('assets/ref/mock.png', png(32, 64, (125, 86, 155, 255)))
            await self.save_asset('assets/char/mock.png', png(32, 64, (125, 86, 155, 255)))

        await self.ensure_style()
        plan = await self.ensure_production_plan()
        # 两条产线分开跑：背景挂了不拖累立绘，反过来也一样。
        report = dict(backgrounds=await self.art_backgrounds(plan),
                      poses=await self.art_poses(plan))
        self.state['art'] = report
        if self.params.get('consistency', True):
            await self.tool('art', 'check_consistency', {})
            await self.tool('art', 'check_style', {})
        await self.publish_nodes(self.graph['nodes'], ('bg', 'chars'))

    async def voice(self):
        """配音阶段：按章分批合成，一句台词一个音频文件。

        没有台词就不生成（避免给空字符串打电话）；`auto_voice=False` 时整段跳过。
        单句失败不算致命：降级记一条「N 条台词合成失败」，其余照常出片。
        """
        await self.agent('voice', 'run', 0.0, '准备分章合成语音')
        if not self.params.get('auto_voice', True):
            self.stages['voice'] = 'off'
            return
        if self.providers.get('voice') is None:
            await self.degrade('voice', self.unavailable.get('voice', '没有可用的语音 provider'))
        if self.mock:
            await self.save_asset('assets/voice/mock.wav', silent_wav())

        listing = await self.tool('writer', 'list_chapters', {})
        chapter_ids = [''] + [chapter['id'] for chapter in listing['chapters']]
        # 配音顺序也听编剧的：他按剧情推进和情绪基调排了批次顺序。
        # 计划里的顺序不完整（比如后来加了章）就退回章节顺序，别漏配。
        plan = self.graph['agent_state'].get('production_plan') or {}
        order = [cid for cid in (plan.get('voice') or {}).get('order') or []
                 if cid in chapter_ids]
        if order and set(order) == set(chapter_ids[1:]):
            chapter_ids = [''] + order
            shown = ' → '.join(order[:8]) + ('…' if len(order) > 8 else '')
            await self.log(f'配音按编剧排产顺序：{shown}')
            notes = (plan.get('voice') or {}).get('notes') or ''
            if notes:
                await self.log(f'编剧对配音基调的要求：{notes}')
        elif order:
            await self.log(f'编剧排产顺序不完整（{len(order)}/{len(chapter_ids) - 1} 章），配音按章节顺序', 'warn')
        for index, chapter_id in enumerate(chapter_ids, 1):
            await self.material('voice', 'batch_synthesize', dict(chapter_id=chapter_id))
            await self.agent('voice', 'run', index / len(chapter_ids), f'配音批次 {index}/{len(chapter_ids)}')
        await self.publish_nodes(self.graph['nodes'], ('lines',))

    async def build(self):
        """装配阶段：整图校验 + 素材齐全检查 + 导出运行时需要的产物。

        这里调 `tool()` 而不是 `material()` —— 校验不过就不该出包，
        必须让异常往上抛，整轮任务标失败。
        """
        await self.agent('build', 'run', 0.0, '校验并装配')
        evidence = await self.build_gate()
        for node in self.graph['nodes']:
            node['status'] = 'done'
        await self.save_graph(self.graph)
        await self.publish_nodes(self.graph['nodes'], ('status',))
        return evidence

    async def build_gate(self):
        await self.tool('build', 'validate_graph', dict(scope='all'))
        await asyncio.to_thread(validate_graph, self.graph, self.root, self.params.get('auto_voice', True))
        return dict(nodes=len(self.graph['nodes']))

    async def review(self):
        """按用户设置汇总各阶段已经跑过的质量检查。"""
        if not self.params.get('final_check'):
            self.stages['review'] = 'off'
            return
        await self.agent('review', 'run', 0.0, '开始检查成品')
        gates = self.state.get('gates', {})
        checked = [label for gate in gates.values() for label in gate.get('checked', [])]
        items = [dict(level='warn', code='STAGE_DEGRADED', where=stage,
                      message=f'{stage}：{self.impacts[stage]}')
                 for stage, status in self.stages.items() if status == 'mock']
        items += [item for gate in gates.values() for item in gate.get('issues', [])]
        errors = sum(item['level'] == 'error' for item in items)
        warns = len(items) - errors
        score = max(0, 100 - 15 * errors - 4 * warns)
        verdict = 'fail' if errors else ('warn' if warns else 'pass')

        head = (items[0].get('message') if items else '') or ''
        if verdict == 'pass':
            summary = f'没发现问题，{len(self.graph["nodes"])} 个节点全部通过'
        elif verdict == 'warn':
            summary = f'{warns} 处小问题，不影响玩：{head[:40]}'
        else:
            summary = f'{errors} 处必须修的问题，第一条：{head[:36]}'

        filed = await self.tool('review', 'file_report', dict(
            verdict=verdict, score=score, summary=summary,
            items=items[:50], checked=checked))
        self.state['review'] = filed['report']
        await self.emit('review', dict(report=filed['report']))

    async def check_dependencies(self, stage):
        inputs = {}
        self.state.setdefault('dependencies', {})[stage] = inputs
        for required in STAGE_CONTRACTS[stage].requires:
            status = self.stages.get(required)
            impact = self.impacts.get(required, '')
            inputs[required] = dict(status=status, impact=impact)
            # ★ 'off' 是**用户主动关掉的阶段**，不算失败，下游要能接受：
            #   以前只放行 voice，于是 auto_art=false 时 build（依赖 art）会
            #   STAGE_PREREQUISITE_FAILED 直接挂掉。art 的 off 也必须放行。
            off_allowed = (
                (required == 'voice' and status == 'off'
                 and not self.params.get('auto_voice', True))
                or (required == 'art' and status == 'off'
                    and not self.params.get('auto_art', True))
            )
            if required not in self.state.get('completed', ()) or (
                    status not in ('real', 'mock') and not off_allowed):
                raise ProviderError('STAGE_PREREQUISITE_FAILED', f'{stage} 缺少可用的 {required} 产出',
                                    f'pipeline.{stage}')
            if status == 'mock':
                await self.log(f'{stage} 使用 {required} 的兜底产出。{impact}', 'info' if self.mock else 'warn')

    async def check_stage(self, stage, evidence):
        if (stage == 'voice' and not self.params.get('auto_voice', True)
                or stage == 'art' and not self.params.get('auto_art', True)
                or stage == 'review' and not self.params.get('final_check')):
            self.stages[stage] = 'off'
            self.impacts[stage] = OFF_IMPACTS[stage]
            return dict(status='off')
        checked, issues = [], []
        scopes = ()
        if stage == 'writer':
            if not self.graph['agent_state'].get('production_plan'):
                await self.ensure_production_plan()
            evidence = check_writer(self.graph)
            if evidence['plan_source'] == 'fallback':
                await self.degrade('writer', '模型未提供可用的导演排产表',
                                   impact='背景、立绘和配音顺序按剧本字段推导')
            checked = ['结构', '排产']
            scopes = ('text', 'scale')
        elif stage == 'art':
            evidence = await asyncio.to_thread(check_art, self.graph, self.root,
                                               self.tools._backgrounds(), self.tools._is_placeholder)
            counts = [(evidence[key], label) for key, label in (
                ('mock_backgrounds', '个背景键'), ('mock_bases', '个角色基底'),
                ('mock_poses', '张节点立绘'), ('mock_ui', '个界面')) if evidence[key]]
            impact = '、'.join(f'{count} {label}' for count, label in counts)
            await self.record_material_impact(stage, f'{impact}使用占位图' if impact else '')
            checked = ['背景', '立绘', '风格']
            scopes = ('consistency',)
        elif stage == 'voice':
            evidence = await asyncio.to_thread(check_voice, self.graph, self.root)
            silent = evidence['silent']
            await self.record_material_impact(stage, f'{silent} 句台词使用按字数估时的静音音频' if silent else '')
            checked = ['配音与时长']
        elif stage == 'build':
            evidence = evidence or await self.build_gate()
            checked = ['装配和资产引用']
        else:
            report = self.state.get('review') or (self.graph['agent_state'].get('review') or {}).get('final')
            if not report or report.get('verdict') not in ('pass', 'warn', 'fail'):
                raise ValueError('缺少可用的汇总质检报告')
            evidence = dict(verdict=report['verdict'])
        for scope in scopes:
            seen = await self.tool('review', 'inspect_graph', dict(scope=scope))
            checked.extend(seen['checked'])
            issues.extend(seen['issues'])
        return dict(status='passed', checked=checked, evidence=evidence, issues=issues)

    async def record_material_impact(self, stage, impact):
        # 以实际引用为准。备用通道或素材库交付真素材时无需报告占位。
        self.impacts.pop(stage, None)
        if impact:
            await self.degrade(stage, '素材包含已落盘的占位文件', impact=impact)
        else:
            self.stages[stage] = 'real'
            self.degraded.pop(stage, None)

    async def run_stage(self, stage, action):
        completed = self.state.setdefault('completed', [])
        gates = self.state.setdefault('gates', {})
        failures = self.state.setdefault('failures', {})
        resumed = stage in completed
        if resumed and gates.get(stage, {}).get('status') in ('passed', 'off'):
            await self.log(f'断点恢复：跳过已完成阶段 {stage}')
            return
        if self.stages.get(stage) == 'failed':
            self.stages.pop(stage)

        try:
            await self.check_dependencies(stage)
            evidence = None
            if resumed:
                await self.log(f'断点恢复：检查已完成阶段 {stage} 的产出')
            else:
                await self.checkpoint(f'{stage}_start', note=f'开始 {stage} 阶段')
                evidence = await action()
            gates[stage] = dict(await self.check_stage(stage, evidence), gate=STAGE_CONTRACTS[stage].gate)
        except Exception as exc:
            self.stages[stage] = 'failed'
            code = exc.payload['code'] if isinstance(exc, ProviderError) else 'STAGE_GATE_FAILED'
            # 失败详情由任务异常事件负责。报告只持久化代码，避免导出供应商响应中的敏感内容。
            failures[stage] = dict(code=code)
            gates[stage] = dict(status='failed', code=code, gate=STAGE_CONTRACTS[stage].gate)
            invalid = {stage}
            for name, contract in STAGE_CONTRACTS.items():
                if invalid.intersection(contract.requires):
                    invalid.add(name)
            completed[:] = [name for name in completed if name not in invalid]
            for dependent in invalid - {stage}:
                gates.pop(dependent, None)
                self.stages.pop(dependent, None)
            await self.save_graph(self.graph)
            if isinstance(exc, (ValueError, OSError)):
                raise ProviderError(code, f'{stage} 阶段关卡失败：{exc}', f'pipeline.{stage}') from exc
            raise

        self.stages.setdefault(stage, 'mock' if stage in self.degraded else 'real')
        failures.pop(stage, None)
        if not resumed:
            completed.append(stage)
        await self.save_graph(self.graph)
        if not resumed:
            await self.checkpoint(f'{stage}_done', note=f'{stage} 阶段完成')
        await self.agent(stage, 'done', 1.0, '阶段完成', stage_status=self.stages[stage],
                         impact=self.impacts.get(stage, ''))

    async def run(self):
        """跑完整条流水线：角色 → 编剧 → 美术 → 配音 → 装配。

        顺序上有两条硬约束（都是踩出来的）：
        · **角色必须先于 writer**：writer 一开始就会自己编角色名，编完了再想
          对上用户给的「男方X / 女方Y」就来不及了。
        · **writer 必须先于美术/配音**：后两者要读 writer 产出的节点和排产表。

        支持断点续跑：`state['completed']` 里有的阶段会跳过；
        `writer` 不在里面时才真的重写，否则从已有图继续。
        """
        # 必须赶在 writer 之前：writer 一开始就会自己编角色名，编完了再想对上
        # 用户传的「男方X / 女方Y」就来不及了。
        if 'writer' not in self.state.get('completed', ()):
            try:
                await self.ensure_characters_from_references()
            except Exception as exc:              # noqa: BLE001
                await self.log(f'参考图预建角色失败（不影响后续）：{type(exc).__name__}: {exc}', 'warn')
        for stage in STAGE_CONTRACTS:
            await self.run_stage(stage, getattr(self, stage))

        # 「逐节点审核」：全流程跑完后，把还没审过的节点交给前端过审。
        # 这里只发事件不阻塞流水线 —— 审核是用户随时要做的动作，
        # 把 job 挂成等待态会让「生成完成」这件事永远不落定。
        if self.params.get('review_per_node'):
            pending = [dict(id=node['id'], type=node['type'],
                            title=node.get('title') or '',
                            text=' / '.join(line['text'] for line in node['lines'])[:120])
                       for node in self.graph['nodes']
                       if not (node.get('review') or {}).get('status')]
            total = len(self.graph['nodes'])
            await self.emit('review_ready', dict(total=total, pending=pending,
                                                 done=total - len(pending)))

        nodes = self.graph['nodes']
        lines = [line for node in nodes for line in node['lines']]
        images = {node['bg']['asset'] for node in nodes}
        images.update(pose['pose'] for node in nodes for pose in node['chars'])
        images.update(character['ref_image'] for character in self.graph['characters'])
        stats = dict(
            nodes=len(nodes),
            edges=len(self.graph['edges']),
            images=len(images),
            voices=sum(bool(line['voice']) for line in lines),
            text_chars=sum(len(line['text']) for line in lines),
            text_lines=len(lines),
            target_minutes=self.params.get('target_minutes', 60),
            cost=0.0,
            elapsed_s=round(time.monotonic() - self.start, 3),
            mock=bool(self.degraded),
            **stage_report(self.graph),
            **self.state.get('writer', {}),
        )
        await self.emit('done', dict(stats=stats, mock=stats['mock'], stages=stats['stages'],
                                     degraded=stats['degraded'], impacts=stats['impacts']))
        return stats
