# -*- coding: utf-8 -*-
"""所有「真 provider」的公共基类与两个小工具。

本文件只关心三件事：错误长什么样、credential 怎么脱敏、HTTP 怎么统一发。
具体某个供应商（LLM / 生图 / TTS / ComfyUI）的调法在各自同目录的文件里。
"""
import re

import httpx


class ProviderError(Exception):
    """带机器可读 code 的供应商错误。上层靠 code 决定要不要降级回 mock。

    为什么不用普通 Exception
    -----------------------
    前端要按 code 显示不同文案（缺 key / 连不上 / 供应商 4xx / 超时），
    还要把 `where` 打进日志定位是哪一步炸的。所以错误必须结构化：
    `payload` 会原样进 SSE 的 error 事件，`status` 是给 HTTP 用的状态码。

    参数
    ----
    code: 机器码，如 PROVIDER_UNREACHABLE / PROVIDER_HTTP_ERROR。
    message: 给人看的中文说明（可以带供应商原文，但已经过 redact）。
    where: 出错位置，格式 `模块.步骤`，如 `llm.call`。
    status: 转成 HTTP 响应时的状态码，默认 502（上游挂了）。
    field: 校验类错误额外指明是哪个字段错了。
    """

    def __init__(self, code, message, where, status=502, field=None):
        super().__init__(message)
        self.payload = dict(code=code, message=message, where=where)
        self.status = status
        if field is not None:
            self.payload['field'] = field


def redact(text, configs):
    """把文本里的凭据抹成 `***`。

    供应商的错误响应经常把请求头原样回显（尤其是 401/403），而我们又会把
    那段错误原文加到 SSE 事件里、写进 run.log —— 不抹的话 key 就跟着日志
    永久留在磁盘上（2026-10-06 那次 347 处泄露就是这么来的）。

    两道清洗：
    · 按字段名判断凭据（key / token / secret / password 出现在字段名里）
    · 兜底把 `Bearer xxx` 整段抹掉，防止凭据藏在别的字段或 URL 里
    """
    text = str(text)
    for cfg in configs.values():
        for key, value in cfg.items():
            if any(x in key.lower() for x in ('key', 'token', 'secret', 'password')) and isinstance(value, str) and value:
                text = text.replace(value, '***')
    return re.sub(r'Bearer\s+\S+', 'Bearer ***', text, flags=re.I)


class BaseProvider:
    """真实 provider 的统一接口。

    约定：
    - `is_available()` 判断「现在能不能真调」（启用 + 有 key + 传输方式支持）；
      不可用时把原因写进 `unavailable_reason()`，给降级日志用。
    - `call()` 真调远端；任何失败都抛 `ProviderError`，**绝静默成功**。
    - 上层（engine.pipeline）捕获异常后降级回 mock，并在事件里标 `mock: true`。
    """

    kind = 'base'

    def __init__(self, cfg, configs=None):
        """cfg 是这一个供应商的配置；configs 是全部供应商的配置（脱敏时要用）。"""
        self.cfg = dict(cfg or {})
        self.configs = configs or {}
        self._reason = ''

    @property
    def enabled(self):
        """用户在设置里显式打开了这个供应商没有。"""
        return bool(self.cfg.get('enabled'))

    @property
    def api_key(self):
        """凭据。去空白是必要的：从设置页复制粘贴常带换行。"""
        return str(self.cfg.get('api_key') or '').strip()

    @property
    def base(self):
        """base_url，去掉尾部斜杠 —— 拼路径时不想出现 `//`。"""
        return str(self.cfg.get('base_url') or '').rstrip('/')

    def is_available(self):
        """现在能不能真调。返回布尔，原因记在 self._reason 里给日志用。"""
        if not self.enabled:
            # 界面上根本没有「启用」开关：用户能表达的只有「填不填 key」。
            # 所以「有 key + 有 base_url 却没启用」几乎都是配置没保存完整，
            # 不是用户想禁用。这里按「填了 key 就是要用」放行，否则会出现
            # 「界面显示已连接、生成却说未启用」这种自相矛盾（踩过）。
            if self.api_key and self.base:
                self._reason = ''
                return True
            self._reason = '未启用'
            return False
        if not self.api_key:
            self._reason = '缺少 API key'
            return False
        if not self.base:
            self._reason = '缺少 base_url'
            return False
        return True

    def unavailable_reason(self):
        """不可用的原因（人话）。没算过就先算一次。"""
        if not self._reason:
            self.is_available()
        return self._reason or '不可用'

    def headers(self):
        """默认鉴权头。没有 key 时给空 dict，不发一个空的 Authorization。"""
        return {'Authorization': 'Bearer ' + self.api_key} if self.api_key else {}

    async def call(self, *args, **kwargs):
        """子类必须实现。基类这里直接报「未实现」，避免忘了覆盖却静默跑空。"""
        raise ProviderError('PROVIDER_NOT_IMPLEMENTED', f'{type(self).__name__} 未实现 call()',
                            self.kind + '.call', 501)


def audio_seconds(wav_bytes):
    """按 WAV 中实际可用的完整采样帧计算时长，非法 WAV 返回 None。

    不能直接信头里的 nframes：流式 WAV 可能声明数 GB 的占位长度，
    而正常 WAV 的 data 之后还可能跟元数据块。所以取「头里声明的」和
    「文件里真有的」两者的较小值，免得算出一个荒谬的时长。
    """
    import io
    import wave
    try:
        source = io.BytesIO(wav_bytes)
        with wave.open(source, 'rb') as stream:
            # wave.open 读完 data 块头后停在 PCM 起点，source.tell() 就是这时的偏移。
            frame_bytes = stream.getnchannels() * stream.getsampwidth()
            available_frames = (len(wav_bytes) - source.tell()) // frame_bytes
            return min(stream.getnframes(), available_frames) / stream.getframerate()
    except Exception:
        return None


async def request(method, url, **kwargs):
    """发一个 HTTP 请求并把两类失败翻译成 ProviderError。

    为什么统一走这个口子
    -------------------
    1. **trust_env=False**：不吃系统代理。用户机器上挂着代理时，
       请求会被悄悄转走，报错还指不到真正原因。
    2. 所有 httpx 异常都要变成带 code 的 ProviderError，否则上层没法按 code 降级。
    3. 错误原文截断到 1000 字：供应商有时回一整页 HTML，塞进事件会把前端撑爆。
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=8), trust_env=False) as client:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
    except httpx.HTTPStatusError as exc:
        raise ProviderError('PROVIDER_HTTP_ERROR', f'HTTP {exc.response.status_code}: {exc.response.text[:1000]}', 'provider.request') from exc
    except httpx.HTTPError as exc:
        raise ProviderError('PROVIDER_UNREACHABLE', f'{type(exc).__name__}: {exc}', 'provider.request') from exc
