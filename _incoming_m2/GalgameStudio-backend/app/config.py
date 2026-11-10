import os
from pathlib import Path

ROOT = Path(os.getenv('GAL_ROOT', Path(__file__).resolve().parents[1])).resolve()
STATE = Path(os.getenv('GAL_STATE_DIR', Path(os.getenv('LOCALAPPDATA', ROOT / '.local')) / 'GalgameStudio')).resolve()
MOCK = os.getenv('GAL_MOCK', '0') == '1'
PORT = int(os.getenv('GAL_PORT', '8790'))
VERSION = '0.1.0'
IMAGE_LOCAL_CONCURRENCY = 1
IMAGE_CLOUD_CONCURRENCY = 4
TTS_CONCURRENCY = 2

DEFAULTS = {
    'qwen_image': dict(enabled=False, api_key='', model='wanx2.1-t2i-plus', base_url='https://dashscope.aliyuncs.com'),
    'bailian_tts': dict(enabled=False, api_key='', model='cosyvoice-v1', voice='longxiaochun', speed=0.92, base_url='wss://dashscope.aliyuncs.com/api-ws/v1/inference'),
    'llm': dict(enabled=False, api_key='', base_url='https://api.deepseek.com/v1', model='deepseek-chat', temperature=0.85),
    'comfyui': dict(enabled=False, base_url='http://127.0.0.1:8188', workflow_bg='workflows/galgame_bg.json', workflow_char='workflows/galgame_char.json', concurrency=1),
    'local_tts': dict(enabled=False, base_url='http://127.0.0.1:9880', engine='gpt-sovits'),
    'local_llm': dict(enabled=False, base_url='http://127.0.0.1:11434/v1', model='qwen2.5:14b'),
}
