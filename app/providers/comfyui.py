"""ComfyUI 本地生图：走本机的 `/prompt` → 轮询 `/history` → `/view` 取图。

这是「本地通道」。设计成**工作流驱动**，不把节点写死在代码里：
  - 工作流 JSON 放 `app/providers/workflows/comfyui_t2i.json`（可随时换模型/节点）；
  - 文件里用占位符 `{{prompt}}` / `{{negative}}` / `{{width}}` / `{{height}}`
    / `{{seed}}` / `{{model}}` / `{{steps}}` / `{{cfg}}`，提交前逐字段替换。
  - 为什么这么做：本机的模型/节点会换（Qwen-Image、SDXL、Flux 各不相同），
    写死节点名的话换模型就得改 Python；换成工作流文件后只改 JSON。

参考图：ComfyUI 支持，但需要先 `/upload/image` 再改工作流里的 LoadImage 节点，
不同工作流的图输入节点 id 不一样。第一版先明确报「本地通道不支持参考图」，
让 FallbackProvider 自动落到云端通道（云端 qwen-image-3.0-pro 支持参考图）。
"""
import asyncio
import json
import random
import time
import uuid
from pathlib import Path

from .base import BaseProvider, ProviderError, request

WORKFLOW_DIR = Path(__file__).resolve().parent / 'workflows'
DEFAULT_WORKFLOW = WORKFLOW_DIR / 'comfyui_t2i.json'

# 提交后等多久放弃（本地首次加载模型很慢，默认给足）
POLL_INTERVAL = 1.0


class ComfyUIImageProvider(BaseProvider):
    kind = 'comfyui'

    # 本地通道能不能直接出透明图，**取决于用户换的工作流**：
    #   - 默认工作流是普通 t2i + SaveImage，输出 RGB；
    #   - 换成带「抠图 / 移除背景 / LayerDiffuse」节点的工作流后就能出 alpha。
    # 所以这里声明 False（保守），抠图仍由 app/assets/cutout.py 做；
    # 工作流里可以用 `{{transparent}}` 占位符拿到 true/false 自己做分支。
    supports_transparency = False

    def workflow_path(self):
        """ComfyUI 工作流文件的路径。"""
        raw = (self.cfg.get('workflow') or '').strip()
        if raw:
            p = Path(raw)
            return p if p.is_absolute() else WORKFLOW_DIR / raw
        return DEFAULT_WORKFLOW

    def is_available(self):
        # 本地服务不需要 key：只要配了 base_url 就算「配置上可用」。
        # 服务到底开没开，靠运行时真调失败 + FallbackProvider 换通道来判断
        # （在这里做同步探测会阻塞事件循环）。
        """能不能真调：启用状态 + 凭据是否齐。"""
        if not self.enabled and not self.base:
            self._reason = '未配置本地地址'
            return False
        if not self.base:
            self._reason = '缺少 base_url（默认 http://127.0.0.1:8188）'
            return False
        if not self.workflow_path().is_file():
            self._reason = f'缺少工作流文件 {self.workflow_path().name}'
            return False
        return True

    def _fill(self, node, prompt, width, height, seed, transparent=False):
        """把工作流里的占位符替换成真值（递归）。"""
        values = {
            '{{prompt}}': prompt,
            '{{negative}}': self.cfg.get('negative') or '',
            '{{width}}': int(width),
            '{{height}}': int(height),
            '{{seed}}': seed,
            '{{model}}': self.cfg.get('model') or '',
            '{{steps}}': int(self.cfg.get('steps') or 25),
            '{{cfg}}': float(self.cfg.get('cfg') or 4.0),
            '{{transparent}}': bool(transparent),
        }
        if isinstance(node, dict):
            return {k: self._fill(v, prompt, width, height, seed, transparent)
                    for k, v in node.items()}
        if isinstance(node, list):
            return [self._fill(v, prompt, width, height, seed, transparent) for v in node]
        if isinstance(node, str) and node in values:
            return values[node]
        return node

    async def call(self, prompt, width=1024, height=1024, timeout=600, *, reference_images=None,
                   seed=None, transparent=False):
        """真调远端；失败一律抛 ProviderError，绝不静默成功。"""
        where = self.kind + '.call'
        if reference_images:
            raise ProviderError('REFERENCE_NOT_SUPPORTED_BY_LOCAL',
                                '本地 ComfyUI 通道这一版还不支持参考图，交给云端通道处理',
                                where, 422)
        if not prompt or not str(prompt).strip():
            raise ProviderError('EMPTY_PROMPT', '生图提示词为空', where, 422)
        wf_path = self.workflow_path()
        if not wf_path.is_file():
            raise ProviderError('COMFYUI_NO_WORKFLOW',
                                f'找不到 ComfyUI 工作流：{wf_path}', where, 500)
        try:
            template = json.loads(wf_path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            raise ProviderError('COMFYUI_BAD_WORKFLOW',
                                f'工作流解析失败：{exc}', where, 500) from exc

        seed = seed if seed is not None else random.randint(1, 2 ** 31 - 1)
        graph = self._fill(template, str(prompt).strip(), width, height, seed, transparent)
        client_id = uuid.uuid4().hex
        started = time.monotonic()

        try:
            resp = await request('POST', self.base + '/prompt',
                                 json={'prompt': graph, 'client_id': client_id},
                                 timeout=60)
        except ProviderError as exc:
            raise ProviderError('COMFYUI_UNREACHABLE',
                                f'连不上本地 ComfyUI（{self.base}）：服务没启动？',
                                where) from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError('COMFYUI_BAD_RESPONSE',
                                f'提交工作流返回不是 JSON：{resp.text[:200]}', where) from exc
        prompt_id = body.get('prompt_id')
        if not prompt_id:
            raise ProviderError('COMFYUI_REJECTED',
                                f'ComfyUI 拒绝了这个工作流：{json.dumps(body, ensure_ascii=False)[:400]}',
                                where, 502)

        # 轮询 /history 直到出现这张图的输出
        deadline = started + max(30, int(timeout))
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                hist = (await request('GET', f'{self.base}/history/{prompt_id}', timeout=30)).json()
            except (ProviderError, ValueError):
                continue                      # 查历史偶发失败不算终止，继续等
            entry = hist.get(prompt_id) or {}
            outputs = entry.get('outputs') or {}
            for node_out in outputs.values():
                images = node_out.get('images') or []
                for img in images:
                    data = await self._download(img, timeout)
                    if data:
                        return data
            status = entry.get('status') or {}
            if status.get('status_str') == 'error' or status.get('completed') is False:
                raise ProviderError('COMFYUI_EXEC_FAILED',
                                    f'ComfyUI 执行工作流失败：{json.dumps(status, ensure_ascii=False)[:400]}',
                                    where, 502)

        raise ProviderError('COMFYUI_TIMEOUT',
                            f'ComfyUI 超过 {int(timeout)} 秒没出图（本地模型首次加载会慢，可以调大超时）',
                            where, 504)

    async def _download(self, image, timeout):
        """把远端产物下载到本地并返回路径。"""
        params = {
            'filename': image.get('filename', ''),
            'subfolder': image.get('subfolder', ''),
            'type': image.get('type', 'output'),
        }
        query = '&'.join(f'{k}={v}' for k, v in params.items() if v)
        try:
            blob = await request('GET', f'{self.base}/view?{query}', timeout=min(120, timeout))
        except ProviderError:
            return None
        data = blob.content
        if data.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8')):
            return data
        return None
