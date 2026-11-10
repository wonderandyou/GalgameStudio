# 交给 Kimi 的任务：让试玩播放器用上真实素材

> 用法：整段复制发给 Kimi。自包含，对方不需要看我们的对话记录。

---

## 项目

路径：`D:\软件\GalgameStudio`

Galgame 制作器。后端已经能生成上千节点、上百个分支点的完整剧本，
**但「试玩」页还是假的** —— 背景是 CSS 画的雨，立绘是占位，一句声音都听不到。

你要做的就是把这个最后一公里接通。

- 前端：**纯 HTML/CSS/JS，无构建步骤**，不许 npm / React / Vue
- 改动范围：**只动 `ui/` 下的文件**，重点 `ui/app.js` 和 `ui/app.css`
- 不要改 `ui/vendor/litegraph.js`
- 不要改后端 `app/`

---

## 现在是什么样（先读代码）

`ui/app.js` 的试玩部分在 `/* ---------------- 试玩播放器 ---------------- */` 这一段，
核心函数 `showNode(id)` 在 2500 行附近：

```js
const bg = $('pBg');
bg.className = 'bg ' + (n.bg === 'dusk' ? 'dusk' : n.bg === 'white' ? 'white' : 'rainy');  // ← CSS 假景
$('pRain').style.display = n.bg === 'rainy' ? '' : 'none';
$('pChar').style.display = n.type === 'ending' ? 'none' : '';
$('plVoice').textContent = n.voice > 0 ? n.voice.toFixed(1) + 's' : '无';   // ← 只显示秒数
typeText($('pTxt'), n.text || '');
```

三处都跟真实素材无关。

---

## 好消息：数据全都还在

`applyGraph(g)`（2286 行起）把后端的原始数据存下来了，你不用改加载逻辑：

```js
bgAsset:   (raw.bg && raw.bg.asset) || '',                        // 背景图路径
charAsset: (raw.chars && raw.chars[0] && raw.chars[0].pose) || '', // 立绘路径
_raw: raw                                                          // 完整原始节点
```

`n._raw` 就是后端返回的那个节点，结构如下（实测数据）：

```json
{
  "id": "n12",
  "type": "scene",
  "title": "片段 11",
  "bg": { "asset": "assets/bg/mock.png", "prompt": "雨夜的窗边，室内灯光微弱", "seed": 0, "provider": "mock" },
  "chars": [{ "id": "heroine", "pose": "assets/ref/mock.png", "pos": "center", "mood": "平静" }],
  "lines": [
    { "who": "heroine", "text": "她把折皱的车票递到我手边，转头去关那扇窗", "voice": "assets/voice/silent_2.000.wav", "dur": 2.0 },
    { "who": "heroine", "text": "雨水打着窗玻璃，我等那点动静过去才敢说话", "voice": "assets/voice/silent_2.000.wav", "dur": 2.0 }
  ],
  "choices": [{ "label": "继续", "to": "n13", "condition": null }]
}
```

注意两个容易踩的点：

1. **一个节点有 1~2 句台词**（1000 节点时实测 1999 句），不是一句
2. `lines[].voice` 是**相对路径**，`dur` 是这句的真实时长

---

## 要做的四件事

### 1. 背景用真图

资源 URL 规则（已实测可用）：

```
{GalAPI.base}/api/assets/{projectId}/{bg.asset}
```

`bg.asset` 自带 `assets/` 前缀，**原样拼上去**，不要去掉。

例：`http://127.0.0.1:8790/api/assets/p_c85fab.../assets/bg/mock.png` → 200

要求：

- 用 `n.bgAsset` 设背景（`background-image` 或底层 `<img>` 都行）
- `projectId` 从当前项目拿（`projectId` 变量）
- **加载失败时保留现有的 CSS 兜底**（雨/黄昏/白），不要出现白底

### 2. 立绘用真图

- 用 `n.charAsset`（同一套 URL 规则）
- 位置按 `n._raw.chars[0].pos`：`left` / `center` / `right`
- 多角色时最多显示 3 个（后端限制就是 3）
- 没有 `charAsset` 或 `type === 'ending'` 时不显示（保持现在隐藏立绘的行为）

### 3. 语音逐句播

**关键：一个节点要按句推进，不是整段一次显示。**

现在的 `typeText($('pTxt'), n.text)` 是一次把两句话打完。改成：

```
第 1 句：播放 lines[0].voice → 打字机显示 lines[0].text → 按 lines[0].dur 计时
第 2 句：同上
两句都放完，才允许点击推进到下个节点
```

要求：

- `<audio>` 元素，`src` 用同一套 URL 规则
- **每次切节点要停掉上一句**，不要叠音
- 没有 `voice` 的句次就只按 `dur` 计时，不要报错
- 打字机效果保留（现在 26ms 一个字），但**不能比语音快太多**也没关系，重点是等 `dur`

### 4. 音画同步

判定「这一句结束」用 `dur`，**不要用 audio 的 ended 事件做唯一依据**
（mock 模式的音频是静音的，但有正确时长；真实音频可能有偏差）。

建议：以 `dur` 为准，audio 只是跟着播。

---

## 一个必须知道的坑

**mock 模式生成的语音是静音 WAV**（文件名 `silent_2.000.wav`），
你自测时**听不到声音是正常的**，不要以为失败了。

怎么判断音频真的在播：

```js
document.querySelector('#player audio').currentTime > 0    // 数字在走
document.querySelector('#player audio').duration > 0      // 有时长
```

---

## 验收要求（必须自己做，不许交给用户）

1. 生成一个项目（界面点「重试生成」，300 节点几秒就好）
2. 进试玩页，确认：
   - **背景是图片**（不是纯色/CSS 雨），且 `img`/`background-image` 的 URL 返回 200
   - **逐句推进**：一个节点里两句台词要分两次点击，不是一次全出来
   - **音频真的在播**：`currentTime` 在增长
   - 分支节点选完后走向正确
   - 结局节点正常收尾
3. **不能弄坏现有功能**：
   - 章节树还在、能折叠能点
   - 800 节点画布仍然流畅（之前测是 54 fps）
   - 五个页面都能切
   - 控制台零报错

验证方式：用 pywebview 起窗口打开 `http://127.0.0.1:8799/`，`evaluate_js` 读 DOM 断言，
或浏览器开发者工具。**贴真实的返回值，不要只说"通过了"。**

---

## 交付

1. 改了哪些文件（路径 + 一句话）
2. 验收 3 项的实测结果（贴 DOM 断言/截图）
3. 已知限制

有拿不准的先问，不要猜着写完。
