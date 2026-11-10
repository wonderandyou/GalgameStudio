import json
import time
from .base import ProviderError, request, redact

async def test_provider(kind, cfg, configs):
    start = time.perf_counter()
    try:
        base = cfg['base_url'].rstrip('/')
        headers = {'Authorization': 'Bearer ' + cfg['api_key']} if cfg.get('api_key') else {}
        if kind in ('llm', 'qwen_image', 'bailian_tts') and not cfg.get('api_key'):
            raise ProviderError('MISSING_API_KEY', '请先配置 API key；未执行远端鉴权测试。', kind + '.test', 400)
        if kind == 'comfyui':
            body = (await request('GET', base + '/system_stats')).json()
            if not isinstance(body, dict) or 'system' not in body or 'devices' not in body:
                raise ProviderError('INVALID_RESPONSE', '响应不是 ComfyUI system_stats', kind + '.test')
            detail = 'ComfyUI /system_stats 返回成功'
        elif kind in ('llm', 'local_llm'):
            body = (await request('GET', base + '/models', headers=headers)).json()
            if not isinstance(body, dict) or not isinstance(body.get('data'), list):
                raise ProviderError('INVALID_RESPONSE', '响应不是 OpenAI 兼容 models 列表', kind + '.test')
            detail = '模型列表可访问；不代表选定模型的生成权限已验证'
        elif kind == 'local_tts':
            body = (await request('GET', base + '/openapi.json')).json()
            route = '/tts' if cfg['engine'] == 'gpt-sovits' else '/infer'
            if route not in body.get('paths', {}):
                raise ProviderError('INVALID_RESPONSE', f'服务未声明 {route} 接口', kind + '.test')
            detail = f'{route} 已发现；尚未执行语音合成'
        elif kind == 'qwen_image':
            body = (await request('GET', base + '/compatible-mode/v1/models', headers=headers)).json()
            if not isinstance(body.get('data'), list):
                raise ProviderError('INVALID_RESPONSE', 'DashScope 模型列表格式异常', kind + '.test')
            detail = 'DashScope 鉴权和模型列表访问成功；未提交收费生图任务，生图模型权限未验证'
        else:
            # bailian_tts 有两条完全不同的协议，不能一律按 WebSocket 测：
            #   · CosyVoice：base_url 是 wss://，走 WebSocket 握手
            #   · qwen3-tts-flash（项目默认）：base_url 是 https://，走普通 HTTPS 鉴权
            # 以前这里不判断，bailian_tts 一律进 WebSocket 握手 —— 于是 https 配置
            # 会被拿去建 ws 连接，DashScope 返回
            #   "Only Uris starting with 'ws://' or 'wss://' are supported"
            # 看起来像「百炼连不上」，实际是测试走了错协议（踩过，主人报的）。
            if base.lower().startswith('ws'):
                from .websocket_probe import handshake
                await handshake(base, cfg['api_key'])
                detail = 'CosyVoice WebSocket 握手成功；未提交收费合成任务，音色权限未验证'
            else:
                body = (await request('GET', base + '/compatible-mode/v1/models',
                                      headers=headers)).json()
                if not isinstance(body.get('data'), list):
                    raise ProviderError('INVALID_RESPONSE', 'DashScope 模型列表格式异常',
                                        kind + '.test')
                detail = ('DashScope HTTPS 鉴权通过；未提交收费合成，'
                          f'音色权限未验证（model={(cfg.get("model") or "?")}）')
        return dict(ok=True, latency_ms=round((time.perf_counter()-start)*1000), detail=detail)
    except Exception as exc:
        error = exc.payload if isinstance(exc, ProviderError) else dict(code='PROVIDER_UNREACHABLE', message=f'{type(exc).__name__}: {exc}', where=kind+'.test')
        error = dict(error, message=redact(error['message'], configs), where=kind+'.test')
        return dict(ok=False, latency_ms=round((time.perf_counter()-start)*1000), detail=error['message'], error=error)
