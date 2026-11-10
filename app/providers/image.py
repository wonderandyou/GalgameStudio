"""DashScope 生图：兼容文生图接口，以及带参考图的多模态接口。

端点：`{base_url}/compatible-mode/v1/images/generations`
请求：`{"model": ..., "prompt": ..., "n": 1, "size": "1024*1024"}`
响应：`{"data": [{"url": ...} | {"b64_json": ...}]}`

带参考图时使用 qwen-image-3.0-pro 的 multimodal-generation 接口。
"""
import asyncio
import base64

from .base import BaseProvider, ProviderError, request


REFERENCE_MODEL = 'qwen-image-3.0-pro'
# ★ 2.1 系列能按提示词自动输出**带 alpha 通道**的图（官方文档 2026-10 版：
#   "automatically decide whether to output a standard image or an image with a
#   transparent (alpha) channel based on the prompt description"）。3.0 系列只能出 RGB。
# 实测（2026-11-09，本机）：直出 3 次全部带透明（全透明像素 15~19%）；
# 拿一张透明立绘做编辑（改表情）也保住了透明，且脸/衣服/构图不变 ——
# 所以立绘**不用再抠图**，也就不会再有白边。
ALPHA_MODEL = 'qwen-image-2.1-pro'
MAX_REFERENCE_IMAGES = 3
MAX_REFERENCE_BYTES = 10_000_000

# 输出边长上下限：百炼对这张模型的尺寸是**自由尺寸**（不是枚举白名单），
# 实测传 1111*777 也直接 200 —— 但太小/太大会被拒或退化成默认值。
# 这里只做「夹到合理范围 + 对齐到 8 的倍数」（生图模型内部按 8/16 降采样）。
MIN_SIDE = 512
MAX_SIDE = 4096


class DashScopeImageProvider(BaseProvider):
    kind = 'qwen_image'

    # 2.1 系列能按提示词出带 alpha 的图（见文件头 ALPHA_MODEL 的说明）。
    # 上层据此决定「立绘直接要透明」还是「生成白底再抠图」——现在走前者，不抠图。
    supports_transparency = True

    def _size(self, width, height):
        # DashScope 生图尺寸用 `宽*高` 写法。
        """按目标比例算出生图尺寸。"""
        width = max(MIN_SIDE, min(MAX_SIDE, int(width)))
        height = max(MIN_SIDE, min(MAX_SIDE, int(height)))
        # 对齐到 8 的倍数：避免模型做内部缩放时把宽高比搞歪（立绘会因此变胖）。
        width -= width % 8
        height -= height % 8
        return f'{width}*{height}'

    async def call(self, prompt, width=1024, height=1024, timeout=180, *, reference_images=None,
                   seed=None, transparent=False):
        """真调远端；失败一律抛 ProviderError，绝不静默成功。"""
        if not prompt or not str(prompt).strip():
            raise ProviderError('EMPTY_PROMPT', '生图提示词为空', self.kind + '.call', 422)
        # 统一走 multimodal-generation（qwen-image-3.0-pro）。
        # 为什么不用 OpenAI 兼容的 /compatible-mode/v1/images/generations：
        #   DashScope 那个端点对 qwen-image 系列不开（实测直接 404），
        #   而 multimodal 这条带不带参考图都能出图，参考图模式已经验证通过。
        #   两条路各写一套只会多一处会坏的分支。
        return await self._multimodal(str(prompt).strip(), width, height, timeout,
                                      reference_images or [], seed, transparent)

    async def _multimodal(self, prompt, width, height, timeout, reference_images, seed=None,
                          transparent=False):
        """带参考图的多模态请求（图生图 / 带参考生成）。"""
        where = self.kind + '.call'
        if len(reference_images) > MAX_REFERENCE_IMAGES:
            raise ProviderError('REFERENCE_LIMIT', f'一次最多传入 {MAX_REFERENCE_IMAGES} 张参考图', where, 422)
        content = []
        for data in reference_images:
            if len(data) > MAX_REFERENCE_BYTES:
                raise ProviderError('REFERENCE_TOO_LARGE', '百炼单张参考图不能超过 10 MB', where, 413)
            if data.startswith(b'\x89PNG\r\n\x1a\n'):
                mime = 'image/png'
            elif data.startswith(b'\xff\xd8'):
                mime = 'image/jpeg'
            else:
                raise ProviderError('REFERENCE_INVALID_IMAGE', '参考图必须为 PNG 或 JPEG', where, 422)
            content.append({'image': f'data:{mime};base64,{base64.b64encode(data).decode("ascii")}'})
        content.append({'text': prompt})
        params = {'size': self._size(width, height), 'n': 1, 'prompt_extend': False, 'watermark': False}
        if transparent:
            # 2.1 系列靠「提示词里怎么说」来决定要不要 alpha，所以这句话必须带上；
            # 传参数没用（参数表里没有 transparent 字段）。
            content[-1] = {'text': prompt + (
                '\n背景必须完全透明：人物之外全部 alpha 通道为 0，'
                '不要白色底、不要渐变、不要地面、不要投影。')}
        if transparent:
            # 能力位为 False 时**不传**任何「透明」字段：百炼会静默忽略未知参数，
            # 传了只会让人以为生效（钱照收，图照样是白底）。真正的透明由
            # app/assets/cutout.py 在后处理里做，那是当前唯一可靠的路。
            if self.supports_transparency:
                params['background'] = 'transparent'
                params['output_format'] = 'png'
        # seed：不传时模型对同一 prompt 会给出**完全一样**的图（实测：重生成出来的
        # 文件名哈希都不变，用户点「重新生成」等于没点）。要重画就传一个新 seed。
        if seed is not None:
            params['seed'] = int(seed) % 2147483647
        payload = {
            'model': ALPHA_MODEL if transparent else REFERENCE_MODEL,
            'input': {'messages': [{'role': 'user', 'content': content}]},
            'parameters': params,
        }
        # 429（限流）和 5xx 是临时错误，退避重试几次再放弃。
        # 实测：连着跑几个任务后百炼会限流，一条 429 就把整个 24 节点任务判死
        # —— 剧本全写完、布局都排好了、图片也出了 12 张，只差最后几张。
        # 另外错误文案按「有没有参考图」区分：之前无参考图也写「参考图生图失败」，
        # 排查时会被带偏。
        label = '参考图生图' if reference_images else '生图'
        response = None
        for attempt in range(1, 4):
            try:
                response = await request('POST', self.base + '/api/v1/services/aigc/multimodal-generation/generation',
                                         json=payload, headers=self.headers(), timeout=timeout)
                break
            except ProviderError as exc:
                upstream = getattr(exc.__cause__, 'response', None)
                status = upstream.status_code if upstream is not None else None
                retryable = status in (429, 500, 502, 503, 504)
                if retryable and attempt < 3:
                    await asyncio.sleep(2 ** attempt)      # 2 秒、4 秒
                    continue
                if status in (404, 405, 501):
                    raise ProviderError('REFERENCE_NOT_SUPPORTED',
                                        f'当前百炼地址不支持 {REFERENCE_MODEL}', where) from exc
                if status == 413:
                    raise ProviderError('REFERENCE_TOO_LARGE',
                                        '百炼拒绝了过大的参考图请求', where, 413) from exc
                reason = f'HTTP {status}' if status is not None else exc.payload['code']
                hint = '（限流，稍后重试即可）' if status == 429 else ''
                raise ProviderError('REFERENCE_GENERATION_FAILED',
                                    f'百炼{label}失败（{reason}）{hint}', where) from exc

        try:
            items = response.json()['output']['choices'][0]['message']['content']
            url = next(item['image'] for item in items if isinstance(item, dict) and item.get('image'))
        except (ValueError, KeyError, IndexError, TypeError, StopIteration) as exc:
            raise ProviderError('REFERENCE_INVALID_RESPONSE', '百炼参考图生图响应没有图片', where) from exc
        if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
            raise ProviderError('REFERENCE_INVALID_RESPONSE', '百炼参考图生图响应没有有效的图片 URL', where)
        try:
            data = (await request('GET', url, timeout=timeout)).content
        except ProviderError as exc:
            raise ProviderError('REFERENCE_DOWNLOAD_FAILED', '百炼参考图生图结果下载失败', where) from exc
        if not data.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8')):
            raise ProviderError('REFERENCE_INVALID_RESPONSE', '百炼返回的不是 PNG/JPEG 图像', where)
        return data
