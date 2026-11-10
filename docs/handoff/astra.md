# Astra 交接 · 2026-11-07

## 我做了什么

1. `app/providers/base.py` 按 WAV 实际能读到的完整采样帧算时长。保留合法短 data 块的边界，避免把尾部元数据计入。
2. `app/api_assets.py`、`app/asset_records.py`、`app/main.py` 接入参考图 POST、GET、DELETE。验证 PNG/JPEG、图片尺寸、类型及 20 MiB 上限。二进制按 SHA-256 前 16 位去重，重复上传可用独立标签登记。DELETE 删除登记并保留文件。`app/mock_api.py` 的现有素材读取路由增加 `.jpg` 和 `.jpeg`。
3. `app/providers/image.py` 增加 `reference_images`，用 `qwen-image-3.0-pro` 把 data URI 和文字一起送进百炼的 `multimodal-generation`。`app/agents/tools.py` 为背景和立绘分别读取最新的最多三张去重场景或角色参考图。带参考图的生成独立于跨项目缓存。`app/engine/pipeline.py` 将参考图错误发为任务错误。
4. `app/assets/cutout.py` 用 Pillow 从四边找连通近白背景，估计 1–2 像素轮廓的透明度并去掉混入的白色。`app/agents/tools.py` 在立绘保存及入库前执行，生图提示要求纯白底。
5. `app/api_assets.py`、`app/asset_records.py` 接入语音 POST、GET、DELETE。WAV 按内容寻址，真实秒数沿用第 1 项。`where.exe ffmpeg` 和项目 `app`、`tools` 下的可执行文件搜索均无命中，非 WAV 返回 `NEED_WAV 415`。
6. `app/api_assets.py` 增加 `/library`，按四类扫描磁盘，返回相对路径、大小和 UTC 修改时间。

`app/astra_probe.py` 提供隔离项目的 multipart 生命周期探针和两张标题立绘的深色叠底对照。探针目录放在系统临时目录，截图通过 `--screens` 指定。

## 验证结果

以下命令的项目目录为 `D:\软件\GalgameStudio`，使用指定的 Python 3.11.9。Pillow 版本输出为 13.0.0。

```powershell
Set-Location 'D:\软件\GalgameStudio'
$env:PYTHONPATH='D:\软件\GalgameStudio'
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$py='D:\软件\GalgameStudio\app\.venv\Scripts\python.exe'
$scratch='C:\Users\q3153\Documents\Codex\2026-11-07\d-galgamestudio-galgame-agent-app-x20\work'
```

| 项 | 实际命令 | 关键输出 |
|---|---|---|
| 1 | `& $py -B -`，输入标准 PCM、流式头、带尾部块与坏文件的内联回归 | `PCM 2.64`，`streaming 2.64`，`trailing metadata 2.64`，`invalid None` |
| 1 | `$env:ASTRA_PROBE='tts'` 后通过 `node -` 执行 `$scratch\astra_dashscope.cjs` 内容 | 百炼 `HTTP 200`，request id `c96b9012-ec0b-9bac-9308-790c766f4556`，下载 142124 字节至 `$scratch\fresh-tts.wav` |
| 1、2、3、5、6 | `& "$scratch\verify-astra-transport.ps1"` | 新鲜百炼 WAV `declared_data=2147483547 seconds=2.96`，24 kHz、16 bit、mono。Python provider 用测试传输验证带图请求、响应取图、原文生图和超限分支。实际 ASGI 请求验证参考图和语音空列表、两种缺失登记 DELETE 404、`INVALID_KIND` 和 `INVALID_IMAGE` 422、`INVALID_AUDIO` 422、`NEED_WAV` 415。`/library` 返回四组各 1 个真实文件，探针项目没有登记表 |
| 2、5、6 | `& $py -m app.astra_probe uploads` | `ImportError: DLL load failed while importing _imaging`。PNG/JPEG 和 WAV 成功写盘后的完整生命周期仍待验收 |
| 3 | `$env:ASTRA_PROBE='image'` 后通过 `node -` 执行 `$scratch\astra_dashscope.cjs` 内容 | 百炼 `HTTP 200`，request id `3d30a8c2-3d1d-94b1-a260-d8fe36ef596f`，`input_image_count=1`，`output_image_count=1`，`1024×1536`，PNG 1496327 字节 |
| 4 | `& $py -m app.astra_probe cutouts --screens "$scratch\cutouts"` | `_imaging` DLL 加载失败。两张样图的透明边缘及白色细节未验收 |
| 回归 | `& $py -m app.agents.registry` | `5 个 agent，29 个工具，skill 齐全` |
| 回归 | `& $py tools\_check_art_skill.py` | `结果: PASS` |
| 回归 | `& $py tests\check_contract.py` | 5 项全 PASS，包括 `1098 graph combinations`、PNG 和 WAV |
| 回归 | `& $py tests\test_m1.py`，将 `TEMP` 和 `TMP` 指向 `$scratch` | `Ran 6 tests in 0.702s`，`FAILED (errors=6)`，均在创建临时目录时失败 |
| 回归 | `& $py -`，通过下方包装运行 `tools\_test_art_style.py`、`tools\_test_ui_agent.py` | 分别在第 30 行和第 43 行的 `WORK.mkdir` 报 `WinError 5` |
| 回归 | `& $py -`，通过同一包装运行 `tools\_probe_tts.py` | `httpx` 导入 `_ssl` 时 DLL 加载失败，原脚本未发出 HTTP 请求 |
| 静态 | `& $py -`，对本轮 10 个 Python 文件执行 `ast.parse` | `PASS parsed 10 changed Python files` |

百炼输出已存 `docs/screens/astra/参考图生图.png`。与 `ui/assets/title/char_l.png` 肉眼对比后，细线、低饱和阴影和日系视觉小说的面部画法相近。短银发、风衣和笔记本按提示出现，同时输出沿用了原图的红领结，说明参考还会带入部分服饰特征。真实云调用用 Node 的 TLS 完成。Python provider 的请求及响应处理另外用测试传输验证。[百炼图像生成与编辑 API](https://help.aliyun.com/zh/model-studio/qwen-image-generation-and-editing-api-reference)

原始美术、界面和 TTS 脚本通过以下包装运行，输出目录指到 scratch。调用时依次传入原脚本与目标路径，分别使用 `art_style_final`、`ui_agent_final` 和 `legacy-tts.wav`。

```powershell
@'
import asyncio
import importlib.util
from pathlib import Path
import sys

path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(path.stem, path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
dest = Path(sys.argv[2])
if path.stem == '_probe_tts':
    module.OUT = dest
    raise SystemExit(module.main())
module.WORK = dest
raise SystemExit(asyncio.run(module.main()))
'@ | & $py - 'tools\_test_art_style.py' "$scratch\art_style_final"
```

## 发现但没改的

- 当前执行环境的 `_imaging`、`_ssl` 无法加载，且指定解释器在隔离 scratch 中创建目录或文件遇到拒绝访问。仓库原有 `docs/backend/verification.md` 也记录了 Windows 执行环境 DLL 加载异常。完整上传、抠图和三项写盘回归待在可写且能加载 DLL 的 Windows 会话重跑。
- `tools/_probe_tts.py` 仍直接使用 `wave.getnframes()` 打印时长，连接恢复后遇到流式假头也会打印错。该脚本还把 WAV 写入 `tools`。请脚本负责人改用 `audio_seconds(data)` 并允许指定输出路径。
- `app/main.py` 的现有语音供应商设置校验只接受 `ws/wss`，`qwen3-tts-flash` 的 HTTP provider 则需要 `https`。从设置界面录入这类地址会得到 422。
- `app/mock_api.py` 的单节点重生成目前仍限 mock。参考图接入正常 `/generate` 流水线。语音登记保存文件和角色关联，图中没有上传音频与百炼克隆音色 ID 的映射。
- 上传契约允许 20 MiB，百炼参考图单张上限 10 MB。10 MB 以上的已上传图片需要压缩后再用于生成。[百炼参考图限制](https://help.aliyun.com/zh/model-studio/qwen-image-generation-and-editing-api-reference)

## 给下一位的注意事项

恢复 DLL 和文件写权限后先跑 `& $py -m app.astra_probe uploads`，再跑 `& $py -m app.astra_probe cutouts --screens 'D:\软件\GalgameStudio\docs\screens\astra'`。打开 `抠图-后.png` 检查发丝、右侧立绘的浅色高光以及衣领，再补跑美术、UI 和 M1 回归。

