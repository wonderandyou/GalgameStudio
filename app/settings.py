"""本机偏好设置。

存在 `%LOCALAPPDATA%\\GalgameStudio\\settings.json`（跟凭据放同一个目录）。

★ 只放「跟机器/数据放哪、界面怎么显示」有关的东西。
  跟某个项目有关的（游戏名、图标、分辨率）在项目自己的 graph.project 里，
  不要塞到这里 —— 换个项目就该换一份。
"""
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)


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


def _frozen() -> bool:
    """是不是 PyInstaller 打出来的 exe。源码运行没有 sys.frozen。"""
    return bool(getattr(sys, 'frozen', False))


_portable_cache: dict = {}


def _portable_projects_dir() -> Path | None:
    """打包版：exe 旁边的 projects（不在 `_internal` 里）。

    ★★ 为什么必须区分（2026-11-11 实测）：
      打包版里 app 的代码被打进 PyInstaller 的 PYZ 归档，运行时
      `__file__` = `<exe目录>\\_internal\\app\\settings.pyc`，
      `parents[1]` 正好是 **`_internal`** ——所以 `ROOT / 'projects'`
      会落在 `_internal\\projects`。

      而用户升级时最常见的动作就是「覆盖解压」，`_internal` 整个被替换，
      **用户几百个项目一次性没了**。这不是理论风险：`build_exe.py` 建了
      exe 旁的空 `projects\\` 并在注释里写着「项目放这里，换电脑拷走跟着走」，
      但程序从来不读它 —— 那段注释一直是假的。

    exe 旁边不可写（比如装在 Program Files）就返回 None，让调用方回退，
    不要在这里抛异常把程序搞起不来。
    """
    if not _frozen():
        return None
    if 'path' in _portable_cache:
        return _portable_cache['path']
    try:
        exe_dir = Path(sys.executable).resolve().parent
    except OSError:
        return None
    target = exe_dir / 'projects'
    try:
        target.mkdir(exist_ok=True)
        probe = target / '.writable'
        probe.write_bytes(b'ok')
        probe.unlink()
    except OSError:
        target = None
    _portable_cache['path'] = target
    return target


def _adopt_bundle_projects(portable: Path) -> None:
    """打包版：把旧版本误存进 `_internal\\projects` 的项目搬到 exe 旁边。

    只在「exe 旁边还没有任何真实项目」时才动 —— 新位置已经有东西就一律不碰，
    避免把用户的项目搞混。

    ★ 用 `Path.rename`（同一块盘上是原子的 rename），不做「复制 + 删」：
      要么改名成功，要么原样不动，不会出现搬一半的残缺目录。
      `_internal` 和 exe 必定同盘，所以这里一定是原子 rename。
    """
    try:
        legacy = ROOT / 'projects'
        if legacy.resolve() == portable.resolve() or not legacy.is_dir():
            return
        for item in portable.iterdir():
            if item.is_dir() and (item / 'project.json').is_file():
                return                                  # 新位置已有真实项目，不动
        moved = 0
        for item in list(legacy.iterdir()):
            target = portable / item.name
            if target.exists():
                continue
            try:
                item.rename(target)
                moved += 1
            except OSError as exc:
                log.warning('旧项目搬不过来 %s：%s: %s', item.name, type(exc).__name__, exc)
        if moved:
            log.warning('已把 %d 个旧项目从 %s 搬到 %s', moved, legacy, portable)
    except OSError as exc:
        log.warning('检查旧项目位置失败：%s: %s', type(exc).__name__, exc)


def projects_dir() -> Path:
    """项目数据放在哪。

    优先级：
      1. 设置里显式指定的 `projects_dir`（用户自己改过就尊重他）
      2. 打包版 → exe 旁的 `projects\\`（不在 `_internal` 里，覆盖升级不丢）
      3. 源码运行 → `<仓库根>/projects`（原地不动，开发时的老位置）
    """
    raw = str(read().get('projects_dir') or '').strip()
    if raw:
        return Path(raw)
    portable = _portable_projects_dir()
    if portable is not None:
        _adopt_bundle_projects(portable)
        return portable
    return ROOT / 'projects'
