import os
from pathlib import Path

ROOT = Path(os.getenv('GAL_ROOT', Path(__file__).resolve().parents[1])).resolve()
STATE = Path(os.getenv('GAL_STATE_DIR', Path(os.getenv('LOCALAPPDATA', ROOT / '.local')) / 'GalgameStudio')).resolve()
# 素材库：跨项目共用。生图前先查这里，命中就不重新生成。
LIBRARY = Path(os.getenv('GAL_LIBRARY_DIR', ROOT / 'library')).resolve()
MOCK = os.getenv('GAL_MOCK', '0') == '1'
PORT = int(os.getenv('GAL_PORT', '8790'))
VERSION = '0.1.0'
IMAGE_LOCAL_CONCURRENCY = 1
IMAGE_CLOUD_CONCURRENCY = 4
TTS_CONCURRENCY = 2

DEFAULTS = {
    'qwen_image': dict(enabled=False, api_key='', model='wanx2.1-t2i-plus', base_url='https://dashscope.aliyuncs.com'),
    # 默认走 qwen3-tts-flash（https，实测可用）。CosyVoice 的 wss:// 需要 websocket 客户端库，
    # 本机没有，所以老默认值 wss 一开就是不可用状态 —— 默认给能直接用的那条路。
    'bailian_tts': dict(enabled=False, api_key='', model='qwen3-tts-flash', voice='Cherry', speed=0.92, base_url='https://dashscope.aliyuncs.com'),
    'llm': dict(enabled=False, api_key='', base_url='https://api.deepseek.com/v1', model='deepseek-chat', temperature=0.85),
    'comfyui': dict(enabled=False, base_url='http://127.0.0.1:8188', workflow_bg='workflows/galgame_bg.json', workflow_char='workflows/galgame_char.json', concurrency=1),
    'local_tts': dict(enabled=False, base_url='http://127.0.0.1:9880', engine='gpt-sovits'),
    'local_llm': dict(enabled=False, base_url='http://127.0.0.1:11434/v1', model='qwen2.5:14b'),
}
