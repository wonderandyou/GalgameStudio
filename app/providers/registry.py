"""provider 注册表：按配置装配真实 provider，并给出「能不能真调」的计划。

`plan()` 是 POST /generate 返回 `mock: true/false` 的依据：
- 三个阶段（writer / art / voice）任一 provider 不可用，就记进 `degraded`；
- 全都不看用时 `mock` 为 True —— 前端和验收能直接看到「这次是降级跑的」。

★ 多通道（2026-11-07 主人定）：**云端优先，本地兜底**。
  每个阶段挂一条通道链，云端不可用或运行中失败 → 自动退到本地；
  两条都挂 → 才记 degraded。回退逻辑在 `fallback.FallbackProvider`。
"""
from .comfyui import ComfyUIImageProvider
from .fallback import FallbackProvider
from .image import DashScopeImageProvider
from .llm import ChatProvider
from .local_tts import LocalTTSProvider
from .tts import BailianTTSProvider

# provider kind → 类
FACTORIES = {
    'llm': ChatProvider,
    'local_llm': ChatProvider,          # 本地 Ollama / vLLM 也是 OpenAI 兼容
    'qwen_image': DashScopeImageProvider,
    'comfyui': ComfyUIImageProvider,    # 本地生图（后备；本机模型未就位时自动跳过）
    'bailian_tts': BailianTTSProvider,
    'local_tts': LocalTTSProvider,      # 本地配音（后备；本机未装服务时自动跳过）
}

# 流水线阶段 → 通道链（**云端在前，本地在后**）
STAGE_KINDS = {
    'writer': ('llm', 'local_llm'),
    'art': ('qwen_image', 'comfyui'),
    'voice': ('bailian_tts', 'local_tts'),
}


def build(kind, configs):
    """按配置建出各 provider 实例。"""
    factory = FACTORIES.get(kind)
    if factory is None:
        return None
    return factory(configs.get(kind) or {}, configs)


def choose(stage, configs):
    """挑出这个阶段**所有配置上可用**的通道，串成回退链。

    返回 (provider, reason)：
      - provider 可能是单个 provider，也可能是 FallbackProvider（多通道时）
      - 一条都不可用 → (None, 每个通道的原因)
    """
    usable, reasons = [], []
    for kind in STAGE_KINDS.get(stage, ()):
        provider = build(kind, configs)
        if provider is None:
            reasons.append(f'{kind}: 未实现')
            continue
        if provider.is_available():
            usable.append(provider)
        else:
            reasons.append(f'{kind}: {provider.unavailable_reason()}')
    if not usable:
        return None, '；'.join(reasons) or '没有可用 provider'
    if len(usable) == 1:
        return usable[0], ''
    # 多条通道 → 包成回退链：运行时前一条失败会自动换下一条
    return FallbackProvider(usable, stage=stage), ''


def plan(configs):
    """返回 {stage: provider|None} + degraded 原因表 + mock 标记。"""
    selected, degraded = {}, {}
    for stage in STAGE_KINDS:
        provider, reason = choose(stage, configs)
        selected[stage] = provider
        if provider is None:
            degraded[stage] = reason
    return dict(providers=selected, degraded=degraded, mock=bool(degraded))


def pipeline_stages():
    """流水线各阶段 → 实际用了哪个模型（real / mock）。"""
    return tuple(STAGE_KINDS)
