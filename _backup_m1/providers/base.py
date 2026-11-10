import re
import httpx

class ProviderError(Exception):
    def __init__(self, code, message, where, status=502):
        super().__init__(message)
        self.payload = dict(code=code, message=message, where=where)
        self.status = status

def redact(text, configs):
    # 供应商错误可能回显凭据，所有输出统一清洗。
    text = str(text)
    for cfg in configs.values():
        for key, value in cfg.items():
            if any(x in key.lower() for x in ('key', 'token', 'secret', 'password')) and isinstance(value, str) and value:
                text = text.replace(value, '***')
    return re.sub(r'Bearer\s+\S+', 'Bearer ***', text, flags=re.I)

async def request(method, url, **kwargs):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=8), trust_env=False) as client:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
    except httpx.HTTPStatusError as exc:
        raise ProviderError('PROVIDER_HTTP_ERROR', f'HTTP {exc.response.status_code}: {exc.response.text[:1000]}', 'provider.request') from exc
    except httpx.HTTPError as exc:
        raise ProviderError('PROVIDER_UNREACHABLE', f'{type(exc).__name__}: {exc}', 'provider.request') from exc
