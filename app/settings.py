"""本机偏好设置。

存在 `%LOCALAPPDATA%\\GalgameStudio\\settings.json`（跟凭据放同一个目录）。

★ 只放「跟机器/数据放哪、界面怎么显示」有关的东西。
  跟某个项目有关的（游戏名、图标、分辨率）在项目自己的 graph.project 里，
  不要塞到这里 —— 换个项目就该换一份。
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    """程序数据目录。

    ★ 跟凭据放同一个地方（config.STATE，受 GAL_STATE_DIR 控制）。
      不要自己拼 %LOCALAPPDATA%\\GalgameStudio —— 实测那个目录下写 .tmp 会被拒
      （PermissionError WinError 13），而凭据那边一直是能写的。
    """
    from .config import STATE
    return Path(STATE)


def settings_file() -> Path:
    """设置文件的完整路径。注意数据目录由 GAL_STATE_DIR 决定，不写死。"""
    return data_dir() / 'settings.json'


def read() -> dict:
    """读设置。文件不存在 / 读不动 / 不是对象，一律当「没有设置」返回空 dict。

    这里故意**不报错**：设置文件坏了不该让程序起不来，用默认值继续跑就行。
    """
    try:
        data = json.loads(settings_file().read_text('utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write(values: dict) -> dict:
    """写设置文件。

    ★ 这里故意不用 app.store.atomic_json —— 那条导入链会带上 providers/windows_crypto，
      在纯脚本环境下实测会卡死（写设置这种小事不值得冒这个险）。
      自己写临时文件再 os.replace，同样是原子的。
    """
    target = settings_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.tmp')
    tmp.write_text(json.dumps(values, ensure_ascii=False, indent=1), 'utf-8')
    os.replace(tmp, target)
    return values


def projects_dir() -> Path:
    """项目数据放在哪。

    没在设置里指定就用 <程序目录>/projects（原来的行为，老用户不受影响）。
    """
    raw = str(read().get('projects_dir') or '').strip()
    if raw:
        return Path(raw)
    return ROOT / 'projects'
