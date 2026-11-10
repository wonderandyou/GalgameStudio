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
            from .websocket_probe import handshake
            await handshake(base, cfg['api_key'])
            detail = 'CosyVoice WebSocket 握手成功；未提交收费合成任务，音色权限未验证'
        return dict(ok=True, latency_ms=round((time.perf_counter()-start)*1000), detail=detail)
    except Exception as exc:
        error = exc.payload if isinstance(exc, ProviderError) else dict(code='PROVIDER_UNREACHABLE', message=f'{type(exc).__name__}: {exc}', where=kind+'.test')
        error = dict(error, message=redact(error['message'], configs), where=kind+'.test')
        return dict(ok=False, latency_ms=round((time.perf_counter()-start)*1000), detail=error['message'], error=error)
