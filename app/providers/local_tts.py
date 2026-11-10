"""本地 TTS：按 OpenAI 兼容的 `/v1/audio/speech` 说话。

本机目前**还没装**本地 TTS（F5-TTS / GPT-SoVITS 都没有），所以这条通道现在
一定不可用 —— 写上是为了让「云端配音不行时退到本地」这条链路有位置，
装好服务、配好端口就能直接生效，不用再改代码。

为什么按 OpenAI 兼容接口写：
  绝大多数本地 TTS 前端（GPT-SoVITS 的 api_v2、CosyVoice 的 openai 兼容层、
  F5-TTS 的 serve、以及各种 one-api 风格网关）都实现了
  `POST /v1/audio/speech`，body 形如
  `{"model": "...", "input": "...", "voice": "...", "response_format": "wav"}`，
  返回音频二进制。这样一套代码能接多种本地服务。
"""
from .base import BaseProvider, ProviderError, audio_seconds, request

# 本地合成的默认输出格式；WAV 最容易算时长，也不依赖额外解码库
DEFAULT_FORMAT = 'wav'


class LocalTTSProvider(BaseProvider):
    kind = 'local_tts'

    def is_available(self):
        """能不能真调：**先看有没有启用**，再看 base_url。

        这里以前漏了 `enabled` 判断，只查 base_url —— 于是「配置里没启用、
        服务也没启动」的这条通道被判成可用，抢走了 voice 阶段（它排在
        bailian_tts 之后，云端一不可用就轮到它），每次配音都去连
        `http://127.0.0.1:9880` 然后报「连不上」。
        界面上看到的「百炼 TTS 连不上」，实际连的是这条本地通道。
        """
        if not self.enabled:
            self._reason = '未启用'
            return False
        if not self.base:
            self._reason = '缺少 base_url（如 http://127.0.0.1:9880）'
            return False
        # 本地服务不需要 api_key
        return True

    async def call(self, text, language='Chinese', timeout=120, voice_id=None):
        """真调远端；失败一律抛 ProviderError，绝不静默成功。"""
        where = self.kind + '.call'
        line = (text or '').strip()
        if not line:
            raise ProviderError('EMPTY_TEXT', '配音文本为空', where, 422)
        payload = {
            'model': self.cfg.get('model') or 'local-tts',
            'input': line,
            'voice': voice_id or self.cfg.get('voice') or 'default',
            'response_format': self.cfg.get('format') or DEFAULT_FORMAT,
        }
        try:
            resp = await request('POST', self.base + '/v1/audio/speech',
                                 json=payload, headers=self.headers(), timeout=timeout)
        except ProviderError as exc:
            raise ProviderError('LOCAL_TTS_UNREACHABLE',
                                f'连不上本地 TTS（{self.base}）：服务没启动？', where) from exc
        data = resp.content
        if not data:
            raise ProviderError('LOCAL_TTS_EMPTY', '本地 TTS 返回空音频', where, 502)
        # 有些服务在失败时返回 JSON 错误体而不是音频
        if data[:1] in (b'{', b'[') and len(data) < 4096:
            raise ProviderError('LOCAL_TTS_ERROR',
                                f'本地 TTS 报错：{data.decode("utf-8", "replace")[:300]}', where, 502)
        return dict(data=data, seconds=audio_seconds(data))
