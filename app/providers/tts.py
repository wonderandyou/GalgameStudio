"""语音 provider：百炼（DashScope）非实时语音合成 Qwen-TTS。

官方接口（2026-11 核对，https://help.aliyun.com/en/model-studio/qwen-tts-api）：

    POST {base}/api/v1/services/aigc/multimodal-generation/generation
    Authorization: Bearer <DASHSCOPE_API_KEY>
    {
      "model": "qwen3-tts-flash",
      "input": {"text": "...", "voice": "Cherry", "language_type": "Chinese"}
    }
    → {"output": {"audio": {"url": "https://...wav", "data": "<base64>"}}}

⚠ 项目默认配置里的 `bailian_tts` 是 CosyVoice 的 **wss:// 流式协议**，
   本机没有 websocket 客户端库（且不允许装包），所以 ws 配置下 `is_available()`
   直接返回 False 并给出原因，由流水线降级到静音占位。
   要走真实合成，把 base_url 改成 `https://dashscope.aliyuncs.com`、
   model 改成 `qwen3-tts-flash`、voice 改成 `Cherry` 之类的 Qwen-TTS 音色。
"""
import base64
import struct

from .base import BaseProvider, ProviderError, audio_seconds, request

TEXT_LIMIT = 600
# 音色配置写错时的兜底音色。qwen3-tts-flash 实测认这一批：
# Cherry / Serena / Ethan / Chelsie / Dylan / Jada / Sunny / Nofish
FALLBACK_VOICE = 'Cherry'


def normalize_wav(data):
    """把 WAV 头里的长度字段改对。

    百炼 TTS 返回的 WAV 头把 RIFF 和 data 的长度都写成 `21474835xx`（流式占位值），
    实际音频只有几秒。播放器大多能按文件末尾容错，但有的会显示「44739 秒」、
    拖进度条发疯，或者直接判定文件损坏 —— 实测（2026-11-09）`wave` 读出来
    是 1073741773 帧，而文件里只有 3.6 秒。

    这里按文件实际长度重写那两个字段；不是 WAV 或本来就是对的，原样返回。
    """
    raw = bytearray(data or b'')
    if len(raw) < 44 or bytes(raw[:4]) != b'RIFF' or bytes(raw[8:12]) != b'WAVE':
        return data
    pos = 12
    while pos + 8 <= len(raw):
        chunk = bytes(raw[pos:pos + 4])
        size = struct.unpack('<I', raw[pos + 4:pos + 8])[0]
        if chunk == b'data':
            real = len(raw) - pos - 8
            if size == real:
                return data                    # 头是对的，别动
            raw[4:8] = struct.pack('<I', len(raw) - 8)
            raw[pos + 4:pos + 8] = struct.pack('<I', real)
            return bytes(raw)
        pos += 8 + size + (size % 2)
    return data



class BailianTTSProvider(BaseProvider):
    kind = 'bailian_tts'

    def is_available(self):
        """能不能真调：启用状态 + 凭据是否齐。"""
        if not super().is_available():
            return False
        if self.base.startswith('ws'):
            self._reason = ('当前 base_url 是 CosyVoice WebSocket 协议（wss://），'
                            '本机没有 websocket 客户端库；请改用 https://dashscope.aliyuncs.com + qwen3-tts-flash')
            return False
        return True

    async def _call_with_voice(self, payload, voice, timeout):
        """发合成请求；音色名不被远端认时，回退兜底音色重试一次。

        为什么要兜底：配置里的 voice 很容易留成别家 TTS 的音色名
        —— 这台机器上就存着 CosyVoice 的 `longxiaochun`，远端直接
        400「Invalid voice specified」，整批配音会全挂，而报错文案里
        看不出问题出在音色上（实测 2026-11-09）。
        """
        url = self.base + '/api/v1/services/aigc/multimodal-generation/generation'
        try:
            return await request('POST', url, json=payload, headers=self.headers(), timeout=timeout)
        except ProviderError as exc:
            message = str(exc.payload.get('message') or exc)
            if voice == FALLBACK_VOICE or 'voice' not in message.lower():
                raise
            payload['input']['voice'] = FALLBACK_VOICE
            return await request('POST', url, json=payload, headers=self.headers(), timeout=timeout)

    async def call(self, text, language='Chinese', timeout=120, voice_id=None):
        """真调远端；失败一律抛 ProviderError，绝不静默成功。"""
        clean = (text or '').strip()
        if not clean:
            raise ProviderError('EMPTY_TEXT', '待合成文本为空', self.kind + '.call', 422)
        if len(clean) > TEXT_LIMIT:
            raise ProviderError('TEXT_TOO_LONG', f'文本超过 {TEXT_LIMIT} 字，需先切句',
                                self.kind + '.call', 422)
        voice = voice_id or self.cfg.get('voice') or FALLBACK_VOICE
        payload = {
            'model': self.cfg.get('model') or 'qwen3-tts-flash',
            'input': {
                'text': clean,
                'voice': voice,
                'language_type': language,
            },
        }
        response = await self._call_with_voice(payload, voice, timeout)
        try:
            body = response.json()
            audio = body['output']['audio']
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError('INVALID_RESPONSE',
                                f'TTS 响应结构异常：{response.text[:300]}',
                                self.kind + '.call') from exc

        data = None
        if audio.get('data'):
            try:
                data = base64.b64decode(audio['data'])
            except (ValueError, TypeError) as exc:
                raise ProviderError('INVALID_RESPONSE', 'TTS audio.data 解码失败',
                                    self.kind + '.call') from exc
        elif audio.get('url'):
            fetched = await request('GET', audio['url'], timeout=timeout)
            data = fetched.content

        if not data:
            raise ProviderError('INVALID_RESPONSE', 'TTS 响应既无 audio.data 也无 audio.url',
                                self.kind + '.call')
        data = normalize_wav(data)
        seconds = audio_seconds(data)
        if seconds is None:
            # 这里只接受能读时长的 WAV；其它格式无法和 graph.dur 对齐，宁可降级。
            raise ProviderError('INVALID_AUDIO', 'TTS 返回的不是可解析的 WAV（无法校验时长）',
                                self.kind + '.call')
        return dict(data=data, seconds=seconds)
