"""文本 provider：OpenAI 兼容 /chat/completions（DeepSeek、OpenAI、本地 Ollama 都走这套）。

真实调用要点：
- 端点 = `{base_url}/chat/completions`（base_url 默认 `https://api.deepseek.com/v1`）
- `trust_env=False` 已在 `base.request` 里固定，避免机器上的代理设置干扰。
- 任何非 2xx / 结构不符都抛 ProviderError，由调用方处理。
"""
import json
import math

from .base import BaseProvider, ProviderError, request


class ChatProvider(BaseProvider):
    kind = 'llm'

    async def call(self, messages, temperature=None, max_tokens=None, timeout=120):
        payload = {
            'model': self.cfg.get('model') or 'deepseek-chat',
            'messages': messages,
        }
        if temperature is not None:
            payload['temperature'] = float(temperature)
        if max_tokens:
            payload['max_tokens'] = int(max_tokens)
        response = await request('POST', self.base + '/chat/completions',
                                 json=payload, headers=self.headers(),
                                 timeout=timeout)
        try:
            body = response.json()
            content = body['choices'][0]['message']['content']
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError('INVALID_RESPONSE',
                                f'LLM 响应结构异常：{response.text[:300]}',
                                self.kind + '.call') from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError('EMPTY_RESPONSE', 'LLM 返回空文本', self.kind + '.call')
        return content.strip()

    async def call_json(self, messages, temperature=None, max_tokens=None):
        """要求模型返回 JSON；解析失败抛 ProviderError（上层降级，不猜格式）。"""
        text = await self.call(messages, temperature=temperature, max_tokens=max_tokens)
        clean = text.strip()
        if clean.startswith('```'):
            # 去掉 markdown 代码围栏
            clean = clean.split('```', 2)[1] if clean.count('```') >= 2 else clean
            if clean.lstrip().lower().startswith('json'):
                clean = clean.lstrip()[4:]
        try:
            return json.loads(clean.strip())
        except ValueError as exc:
            # 报错里必须带上「总长度」和「结尾」：
            # 截断和格式错的现场完全不一样，只给开头 300 字分辨不出来
            # （踩过：模型输出写到 "act": 1, 就断了，但报错只显示开头，
            #   看着像合法 JSON，白查了一轮）。
            tail = text[-200:] if len(text) > 200 else ''
            raise ProviderError(
                'INVALID_JSON',
                f'LLM 未返回合法 JSON（共 {len(text)} 字符，'
                f'若结尾是半截字段多半被 max_tokens 截断）：'
                f'开头 {text[:200]}' + (f' …… 结尾 {tail}' if tail else ''),
                self.kind + '.call') from exc

    async def call_with_tools(self, messages, tools, temperature=None, max_tokens=None,
                              timeout=180, tool_choice='auto'):
        """返回普通文本和解析后的函数调用参数，保留原始调用 ID。"""
        where = self.kind + '.call_with_tools'
        try:
            if not isinstance(messages, list) or not messages or not all(
                isinstance(message, dict) and isinstance(message.get('role'), str)
                and message['role'].strip() for message in messages
            ):
                raise ValueError('messages 必须是非空消息列表，每条消息须有 role')
            if not isinstance(tools, list) or not tools:
                raise ValueError('tools 必须是非空函数定义列表')
            names = {_tool_name(tool) for tool in tools}
            if len(names) != len(tools):
                raise ValueError('工具名称不能重复')
            if isinstance(tool_choice, dict):
                if _tool_name(tool_choice) not in names:
                    raise ValueError('tool_choice 指定的函数必须出现在 tools 中')
            elif tool_choice not in ('auto', 'none', 'required'):
                raise ValueError('tool_choice 必须是 auto、none、required 或函数选择对象')
            payload = {
                'model': self.cfg.get('model') or 'deepseek-chat',
                'messages': messages,
                'tools': tools,
                'tool_choice': tool_choice,
            }
            if temperature is not None:
                temperature = float(temperature)
                if not math.isfinite(temperature) or not 0 <= temperature <= 2:
                    raise ValueError('temperature 必须在 0 到 2 之间')
                payload['temperature'] = temperature
            if max_tokens is not None:
                if type(max_tokens) is not int or max_tokens < 1:
                    raise ValueError('max_tokens 必须是正整数')
                payload['max_tokens'] = max_tokens
            timeout = float(timeout)
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError('timeout 必须是正数')
            response = await request('POST', self.base + '/chat/completions',
                                     json=payload, headers=self.headers(), timeout=timeout)
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise ProviderError('INVALID_ARGUMENT', f'LLM 工具请求参数无效：{exc}',
                                where, status=400) from exc

        try:
            choice = response.json()['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise ProviderError('TRUNCATED_RESPONSE', 'LLM 工具响应超出输出长度限制', where)
            message = choice['message']
            content = message.get('content')
            if content is not None and not isinstance(content, str):
                raise ValueError('content 必须是字符串或 null')
            text = (content or '').strip() or None
            calls = message.get('tool_calls')
            if calls is None:
                calls = []
            if not isinstance(calls, list):
                raise ValueError('tool_calls 必须是列表')

            parsed = []
            seen_ids = set()
            for call in calls:
                call_id = call['id']
                if not isinstance(call_id, str) or not call_id.strip() or call_id in seen_ids:
                    raise ValueError('每个工具调用必须包含唯一的非空 id')
                name = _tool_name(call, default_type='function')
                raw = call['function']['arguments']
                if not isinstance(raw, str):
                    raise ValueError('工具参数必须是 JSON 字符串')
                arguments = json.loads(raw)
                if not isinstance(arguments, dict):
                    raise ValueError('工具参数必须是 JSON 对象')
                json.dumps(arguments, allow_nan=False)
                parsed.append({'id': call_id, 'name': name, 'arguments': arguments})
                seen_ids.add(call_id)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, RecursionError) as exc:
            raise ProviderError('INVALID_RESPONSE', f'LLM 工具响应结构异常：{exc}', where) from exc
        if text is None and not parsed:
            raise ProviderError('EMPTY_RESPONSE', 'LLM 未返回文本或工具调用', where)
        return {'text': text, 'tool_calls': parsed}


def _tool_name(tool, default_type=None):
    if not isinstance(tool, dict) or tool.get('type', default_type) != 'function':
        raise ValueError('只支持 function 类型的工具')
    function = tool.get('function')
    name = function.get('name') if isinstance(function, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise ValueError('工具必须包含非空的 function.name')
    return name
