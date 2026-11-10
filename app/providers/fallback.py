"""多通道回退：一个阶段挂多个 provider，**运行中失败自动换下一个**。

设计动机（主人 2026-11-07 指出）：
    「生图走双通道，本地不行直接走云端，应该有一个判断层」

原来 registry 的两个缺口：
  1. STAGE_KINDS 每个阶段只挂一个 kind（art 只有 qwen_image，voice 只有 bailian_tts）
  2. choose() 只在**开头**挑一次，运行中一旦失败就整轮报错，不会换通道

这个类补的是第 2 点：把「配置上可用」的 provider 串成链，
调用时依次尝试，某个失败就记下原因换下一个；全失败才抛错，
错误信息里带上每一条失败原因（排查时能看到到底卡在哪条通道）。

为什么要单独一层而不是在调用处写 for 循环：
  - 生图 / 配音 / 写作三个阶段的调用签名不同，但「依次试 + 汇总错误」的逻辑一样
  - 上层（pipeline）只需要当成一个普通 provider 用，不用关心底下有几条通道
"""
from .base import BaseProvider, ProviderError


INVALID_MODEL_OUTPUT = {
    'EMPTY_RESPONSE', 'INVALID_JSON', 'INVALID_RESPONSE', 'TRUNCATED_RESPONSE',
}


class FallbackProvider(BaseProvider):
    """把多个 provider 串成「谁行谁上」的链。"""

    def __init__(self, providers, stage=''):
        self.providers = list(providers or [])
        self.stage = stage
        self.kind = f'fallback[{stage}]' if stage else 'fallback'
        self.cfg = {}
        self.configs = {}
        self._reason = ''
        self.last_used = None          # 最近一次真正出结果的 provider kind

    # —— 可用性：链里只要有一个能用，这条链就能用 ——
    def is_available(self):
        """能不能真调：启用状态 + 凭据是否齐。"""
        return any(p.is_available() for p in self.providers)

    def unavailable_reason(self):
        """不可用的原因（人话），给降级日志用。"""
        if not self.providers:
            return '没有候选 provider'
        return '；'.join(f'{p.kind}: {p.unavailable_reason()}' for p in self.providers)

    def chain(self):
        """候选通道名（给日志/前端展示用）。"""
        return [p.kind for p in self.providers]

    async def call(self, *args, **kwargs):
        """真调远端；失败一律抛 ProviderError，绝不静默成功。"""
        return await self._call('call', *args, **kwargs)

    async def call_json(self, *args, **kwargs):
        """按通道解析 JSON，使格式有误的云端响应也能切到本地重试。"""
        return await self._call('call_json', *args, **kwargs)

    async def call_with_tools(self, *args, **kwargs):
        """给旧编剧流程保留工具调用接口，让它沿用同一组文本通道。"""
        return await self._call('call_with_tools', *args, **kwargs)

    async def _call(self, method, *args, **kwargs):
        """统一切换通道，并在文本响应无效时保留原错误供编剧修正。"""
        where = f'{self.kind}.{method}'
        if not self.providers:
            raise ProviderError('NO_PROVIDER', f'{self.stage} 阶段没有任何候选 provider',
                                where)
        failures = []
        last_exc = None
        invalid_output = None
        for provider in self.providers:
            if not provider.is_available():
                failures.append(f'{provider.kind}: {provider.unavailable_reason()}')
                continue
            try:
                result = await getattr(provider, method)(*args, **kwargs)
            except ProviderError as exc:
                # ProviderError 只暴露 payload / status，**没有 .message 属性** ——
                # 之前这里写 exc.message，任何 provider 失败都会变成
                # AttributeError: 'ProviderError' object has no attribute 'message'，
                # 把真实错误盖掉，整轮生成标记失败（主人实测踩到：
                # 剧本全写完、8 张图都出了，最后却报这个错）。
                # message 就是 Exception 的 args[0]，用 str(exc) 最稳。
                failures.append(f'{provider.kind}: {str(exc)}')
                last_exc = exc
                if method != 'call' and exc.payload['code'] in INVALID_MODEL_OUTPUT:
                    invalid_output = exc
                continue
            except Exception as exc:                            # noqa: BLE001
                # 连 ProviderError 都不是（连接断开 / 超时 / 解析炸）也要换下一条，
                # 否则一个通道的意外异常会把整轮生成拖死 —— 这正是要防的情况。
                failures.append(f'{provider.kind}: {type(exc).__name__}: {exc}')
                last_exc = exc
                continue
            self.last_used = provider.kind
            return result
        if invalid_output is not None:
            raise invalid_output
        detail = '；'.join(failures) or '所有通道都不可用'
        raise ProviderError(
            'ALL_CHANNELS_FAILED',
            f'{self.stage} 阶段所有通道都失败：{detail}',
            where,
            502,
        ) from last_exc
