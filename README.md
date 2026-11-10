# 奇迹工坊 · Galgame Studio

一个 galgame 制作器：左边填需求，Agent 生成图片 / 文案 / 配音，右边自动长出可编辑的剧情分支图，最后导出成能玩的游戏。

---

## 现在能做什么

| 环节 | 状态 | 说明 |
|---|---|---|
| 接口配置 | ✅ | 6 类 provider（云端 3 + 本地 3），可测试连接、可保存到后端 |
| 剧情编排 | ✅ | 拖拽 / 连线 / 缩放 / 撤销重做 / 双击改台词 / 自动排布 |
| 生成 | ✅ | 后端 SSE 推进度，画布按节点实时刷新（当前是 mock 后端） |
| 试玩 | ✅ | 打字机、选项分支、回退上一句、重来 |
| 导出 Ren'Py | ✅ | 真的生成 `game/script.rpy` + 占位素材，工程自检通过 |
| 真实模型生成 | ⏳ | 等后端 M3（接千问 / ComfyUI / TTS） |

---

## 怎么打开

**直接双击 `ui\index.html`** 就能用（不需要装任何东西）。

首次进来在「接口配置」页。右下角有两个开关：
- **模拟数据**：完全离线，所有生成都是前端演的，适合看界面
- **真后端**：连 `127.0.0.1:8790`，能真正读写配置

---

## 三个后端，按需要选

### 1. mock 后端（推荐先用这个看完整流程）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-mock.ps1
```

然后打开 `ui\index.html?mode=real&base=8791`

它把契约里的 M2 接口全实现了：建项目 / 生成 / SSE / 读图 / 导出。
**导出会真的写文件**到 `out\<项目名>\game\`，用 Ren'Py SDK 打开就能跑。

> 这是演示用的，不是最终后端。端口 8791。

### 2. 真后端 M1（GPT 交的第一版）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-backend.ps1
```

端口 8790，实现了配置读写 + 连接探测。没有生成 / 导出（那是 M2 的事）。

> 注意：它默认把凭据写在 `%LOCALAPPDATA%\GalgameStudio`，
> 但这台机器那个路径**能建目录不能写文件**（WinError 5），
> 所以启动脚本用 `GAL_STATE_DIR` 指到了项目内的 `.state`。

### 3. 前端自己（不连任何后端）

双击 `ui\index.html`，保持「模拟数据」即可。

---

## 目录

```
GalgameStudio/
├── ui/                     前端（纯静态，双击 index.html）
│   ├── index.html
│   ├── app.css             浅色 + 液态玻璃 + 低功耗
│   ├── app.js              编辑器 / 试玩 / 生成 / Ren'Py 预览
│   └── api.js              mock / real 双模式接口层
├── app/                    后端（GPT 的 M1）+ 独立 venv
├── tools/
│   ├── mock_server.py      mock 后端（M2 参考实现）
│   ├── e2e_test.ps1        端到端验收（12 项）
│   ├── verify_rpy.py       Ren'Py 工程自检
│   ├── verify_wheels.py    依赖哈希校验
│   ├── check_ui.js         DOM 引用自检
│   └── stub_server.py      早期桩（已被 mock_server 取代）
├── scripts/
│   ├── run-backend.ps1     启动真后端
│   └── run-mock.ps1        启动 mock 后端
├── out/                    导出产物
├── docs/screens/           界面截图
├── 方案.md                 完整方案
├── 提示词-后端.md           给后端 AI 的任务书（契约在这里）
├── 给GPT的下一批任务.md
└── 环境说明.md              解释器 / 依赖 / 两个已踩的坑
```

---

## 验收

```powershell
# 端到端（对 mock）
powershell -ExecutionPolicy Bypass -File tools\e2e_test.ps1 -Base http://127.0.0.1:8791
# 端到端（对真后端，M2 未实现的部分会显示 SKIP）
powershell -ExecutionPolicy Bypass -File tools\e2e_test.ps1

# Ren'Py 工程自检
app\.venv\Scripts\python.exe tools\verify_rpy.py out\<项目名>
```

当前基线：

| 后端 | 结果 |
|---|---|
| mock（8791） | `PASS=12  FAIL=0  SKIP=0` |
| 真后端 M1（8790） | `PASS=5   FAIL=0  SKIP=5` |

前端还有几个自检入口（写进 `document.title`，用 `--dump-dom` 抓）：

```
?selftest=1    接口配置收集 / 节点数
?undotest=1    撤销重做
?edittest=1    双击改台词
?playtest=1    试玩回退 / 重来
?exporttest=1  导出链路
?lowpower=1    低功耗模式
```

---

## 快捷键

| 键 | 作用 |
|---|---|
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 |
| `Ctrl+D` | 复制节点 |
| `Delete` | 删除节点 |
| `Esc` | 关闭面板 / 退出试玩 |

编辑器：拖动节点移动 · 拖空白平移 · 滚轮缩放 · 点输出口再点目标节点连线 · 双击台词就地改。
