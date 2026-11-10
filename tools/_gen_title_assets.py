"""批量生成启动界面素材（百炼 qwen-image-3.0-pro）。

用法：
    python tools/_gen_title_assets.py

存到 ui/assets/title/：
    bg_city.png   远景城市（方案 B/D 底图）
    char_l.png    左侧立绘（白底，靠 CSS multiply 融进纸底）
    char_r.png    右侧立绘（白底）
key 从 ~/.dsh/.credentials.yaml 现场读，不落代码、不打印。
"""
import concurrent.futures as cf
import pathlib
import re
import sys
import time

import httpx

CRED = pathlib.Path.home() / '.dsh' / '.credentials.yaml'
OUT_DIR = pathlib.Path(r'D:\软件\GalgameStudio\ui\assets\title')
URL = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
MODEL = 'qwen-image-3.0-pro'

# 统一风格前缀（与 art skill / ART_RULES 同一口径）
STYLE = ('画法赛璐璐，细描线，色块干净，光影中性色调，中对比，柔和顶光，'
         '色调#1f2430 #2b3245 #c4564b #e8e6e3。')

JOBS = [
    dict(
        name='bg_city', size='1280*720',
        prompt=STYLE + '雨夜港口城市远景俯瞰，密集的屋顶与暖黄窗光，海雾与雨丝，'
                       '远处灯塔光晕，冷蓝主调配暖黄点缀，场景，无人，无文字，横向构图，上方留白',
    ),
    dict(
        name='char_l', size='1024*1536',
        prompt=STYLE + 'Galgame 角色半身立绘，十几岁少女，黑色长发扎低马尾，深蓝学生制服长袖，'
                       '站姿正面，双手自然垂在身前，安静微低头，表情平静，'
                       '纯白色纯色背景，人物整体完整、头顶与肩肘不出画面，无文字无logo无装饰',
    ),
    dict(
        name='char_r', size='1024*1536',
        prompt=STYLE + 'Galgame 角色半身立绘，二十岁左右女性，暗红围巾，深灰长风衣，'
                       '站姿正面，微微侧头看向前方，表情沉静，'
                       '纯白色纯色背景，人物整体完整、头顶与肩肘不出画面，无文字无logo无装饰',
    ),
]


def load_key():
    env = __import__('os').environ.get('DASHSCOPE_API_KEY', '').strip()
    if env:
        return env
    for line in CRED.read_text(encoding='utf-8', errors='replace').splitlines():
        m = re.match(r'^\s*DASHSCOPE_API_KEY\s*:\s*(.+?)\s*$', line)
        if m:
            return m.group(1).strip().strip('"\'')
    raise SystemExit('取不到 DASHSCOPE_API_KEY')


def one(job, key):
    t0 = time.time()
    payload = {
        'model': MODEL,
        'input': {'messages': [{'role': 'user', 'content': [{'text': job['prompt']}]}]},
        'parameters': {'size': job['size'], 'n': 1, 'prompt_extend': True, 'watermark': False},
    }
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    try:
        with httpx.Client(timeout=httpx.Timeout(300, connect=15), trust_env=False) as c:
            r = c.post(URL, json=payload, headers=headers)
            if r.status_code != 200:
                return job['name'], False, f'HTTP {r.status_code}: ' + r.text.replace(key, '***')[:400]
            body = r.json()
            contents = body['output']['choices'][0]['message']['content']
            img_url = next((i['image'] for i in contents if isinstance(i, dict) and i.get('image')), '')
            if not img_url:
                return job['name'], False, 'content 无 image 字段'
            img = c.get(img_url)
            img.raise_for_status()
            data = img.content
    except Exception as exc:                                   # noqa: BLE001
        return job['name'], False, f'{type(exc).__name__}: {exc}'

    if not (data.startswith(b'\x89PNG') or data.startswith(b'\xff\xd8')):
        return job['name'], False, '返回不是图片字节'
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{job['name']}.png"
    path.write_bytes(data)
    return job['name'], True, f'{len(data)/1024:.0f} KB  {time.time()-t0:.0f}s  -> {path.name}'


def main():
    key = load_key()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f'[开始] 并发 {len(JOBS)} 张，key 长度 {len(key)}', flush=True)
    ok = bad = 0
    with cf.ThreadPoolExecutor(max_workers=len(JOBS)) as pool:
        futures = {pool.submit(one, job, key): job['name'] for job in JOBS}
        for fut in cf.as_completed(futures):
            name, good, msg = fut.result()
            print(('[OK]  ' if good else '[FAIL]') + f' {name:<8} {msg}', flush=True)
            ok += good
            bad += (not good)
    print(f'[汇总] 成功 {ok} / 失败 {bad}')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
