import asyncio
import os
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException
from .config import DEFAULTS, MOCK, PORT, VERSION
from .providers.base import ProviderError, redact
from .providers.probe import test_provider
from .store import Credentials

app = FastAPI(title='鲸落工坊 Galgame Studio', version=VERSION)
# 最外层 CORS 在文件末尾统一安装，包含异常响应。
credentials = Credentials()

@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({'error': dict(code='HTTP_ERROR', message=str(exc.detail), where='api.routing')}, status_code=exc.status_code)

@app.exception_handler(Exception)
async def internal_error(request, exc):
    return JSONResponse({'error': dict(code='INTERNAL_ERROR', message='内部处理失败，请检查本机凭据文件和运行环境。', where='api.internal')}, status_code=500)

@app.exception_handler(ProviderError)
async def provider_error(request, exc):
    try:
        configs = credentials.read()
    except ProviderError:
        configs = {}
    payload = dict(exc.payload, message=redact(exc.payload['message'], configs))
    return JSONResponse({'error': payload}, status_code=exc.status)

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # 不回显请求体，避免校验错误泄露输入的 key。
    first = exc.errors()[0]
    field = '.'.join(str(x) for x in first['loc'] if x != 'body') or 'body'
    return JSONResponse({'error': dict(code='INVALID_REQUEST', message='请求参数格式错误', where='api.validation', field=field)}, status_code=422)

@app.get('/api/health')
async def health():
    return dict(ok=True, version=VERSION, mock=MOCK)

@app.get('/api/providers')
async def providers():
    return await asyncio.to_thread(credentials.masked)

@app.put('/api/providers')
async def save_providers(body: dict):
    for kind, cfg in body.items():
        if kind not in DEFAULTS or not isinstance(cfg, dict):
            raise ProviderError('INVALID_CONFIG', '未知 provider 或配置格式错误', 'providers.save', 422, field=kind)
        for key, value in cfg.items():
            if key not in DEFAULTS[kind]:
                raise ProviderError('INVALID_CONFIG', f'未知配置字段 {kind}.{key}', 'providers.save', 422, field=f'{kind}.{key}')
            default = DEFAULTS[kind][key]
            if type(value) is not type(default) and not (type(default) is float and type(value) in (float, int)):
                raise ProviderError('INVALID_CONFIG', f'字段类型错误 {kind}.{key}', 'providers.save', 422, field=f'{kind}.{key}')
        if 'base_url' in cfg:
            try:
                url = urlsplit(cfg['base_url'])
                url.port
            except ValueError as exc:
                raise ProviderError('INVALID_CONFIG', 'base_url 格式错误', 'providers.save', 422, field=f'{kind}.base_url') from exc
            schemes = ('wss', 'ws') if kind == 'bailian_tts' else ('https', 'http')
            if url.scheme not in schemes or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ProviderError('INVALID_CONFIG', 'base_url 必须是不含凭据、查询串的服务地址', 'providers.save', 422, field=f'{kind}.base_url')
        if cfg.get('concurrency', 1) != 1:
            raise ProviderError('INVALID_CONFIG', '本地生图并发必须为 1', 'providers.save', 422, field=f'{kind}.concurrency')
        if 'speed' in cfg and not 0.5 <= cfg['speed'] <= 2:
            raise ProviderError('INVALID_CONFIG', 'speed 必须在 0.5 到 2 之间', 'providers.save', 422, field=f'{kind}.speed')
    return await asyncio.to_thread(credentials.write, body)

@app.post('/api/providers/{kind}/test')
async def probe(kind: str):
    if kind not in DEFAULTS:
        raise ProviderError('UNKNOWN_PROVIDER', '未知 provider', 'providers.test', 404)
    configs = await asyncio.to_thread(credentials.read)
    return await test_provider(kind, configs[kind], configs)

from .mock_api import router, lifespan
app.include_router(router)
app.router.lifespan_context = lifespan
# 必须包在 FastAPI 的 ServerErrorMiddleware 外层，连未处理异常的 500 也带 CORS。
api = app
app = CORSMiddleware(api, allow_origins=['*'], allow_credentials=False,
                    allow_methods=['GET', 'PUT', 'POST', 'OPTIONS'],
                    allow_headers=['Content-Type', 'Last-Event-ID'])

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app.main:app', host='127.0.0.1', port=PORT)
