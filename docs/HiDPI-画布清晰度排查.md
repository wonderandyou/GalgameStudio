# Canvas 自绘界面「发虚 / 糊」排查手册

> 起因：2026-11-06 GalgameStudio 节点画布文字发虚。
> 一句话结论：**绝大多数情况是位图分辨率没跟上屏幕缩放比（devicePixelRatio）。**
> 本文不限于 LiteGraph，任何 canvas 自绘界面（音游、图表、编辑器、看板）都适用。

---

## 一、先分清是哪种「糊」

| 现象 | 病因 | 去哪查 |
|---|---|---|
| **整体像蒙一层雾**，文字笔画粘连、圆角发毛 | 位图按 CSS 尺寸开，被浏览器放大 dpr 倍 | ★ 本文 §二（最常见，占九成） |
| 只有**某个缩放比例下**糊 | 文字被 `ctx.scale()` 重采样 | §四.3 |
| 文字**发彩边**（红蓝镶边） | 深色底上用了 ClearType 次像素渲染 | 换灰度抗锯齿 |
| **整块空白但代码没错** | 画在离屏 canvas 上 / 截图工具截不到 | §五 |
| 边缘**只糊一侧**，或位置偏移 | CSS 与位图尺寸不一致 | §三.2 |

排查顺序：**先算 dpr，再看位图尺寸，最后看变换注入点。** 别一上来改字体。

---

## 二、根因：位图 vs 屏幕缩放（核心）

### 现象

用户的屏幕是 **150% 缩放**，`window.devicePixelRatio === 1.5`。
如果 canvas 这样开：

```js
canvas.width  = cssWidth;    // 1322  ← 只有 CSS 那么多像素
canvas.height = cssHeight;   // 858
```

浏览器会把这 1322×858 的位图**拉伸**到 1983×1287 的物理像素上显示。
位图只有那么多信息，拉出来就是糊的 —— 跟把 720p 视频全屏播一样。

### 正确做法：位图与 CSS 尺寸分离

```js
const dpr = window.devicePixelRatio || 1;

canvas.width  = Math.round(cssWidth  * dpr);   // 位图 = 物理像素
canvas.height = Math.round(cssHeight * dpr);

canvas.style.width  = cssWidth  + 'px';        // 显示尺寸 = 逻辑像素
canvas.style.height = cssHeight + 'px';
```

**两者必须分开**：
- 位图管清晰度
- CSS 尺寸管布局和鼠标坐标

分开之后，鼠标事件拿到的 `clientX`（CSS 像素）和绘制用的物理像素各归各的，不会打架。

### 绘制时补缩放

位图变大了，绘制坐标还是逻辑坐标，所以要在应用变换的地方补一个：

```js
ctx.scale(dpr, dpr);
```

这样：`逻辑坐标 × dpr = 物理像素`，1:1 对齐，文字由浏览器在设备分辨率下重新光栅化 → 清晰。

---

## 三、找「变换注入点」——最容易翻车的一步

补 `ctx.scale(dpr)` 的位置选错，画面会整个跑偏或直接空白。原则：

> **要在「框架自己设置画布变换」的那一刻注入，而且那个位置外面得包着 `save/restore`。**

### 3.1 错误示范

```js
// ✗ 包最外层 draw()：内部可能 setTransform 重置，把你清掉
const origDraw = canvas.draw;
canvas.draw = function () {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return origDraw.apply(this, arguments);
};
```

**为什么不行**：很多框架在绘制中途会 `ctx.setTransform(1,0,0,1,0,0)`（重置为恒等矩阵）。
LiteGraph 就是如此 —— `drawBackCanvas` 尾部有一行：

```js
// litegraph.js L8409-8413
if (!this.viewport) {
    ctx.restore();
    ctx.setTransform(1, 0, 0, 1, 0, 0);   // ← 你的 dpr 被冲掉了
}
```

冲掉之后所有内容按 1:1 画，跟放大的位图对不上 → 空白或者错位。

### 3.2 正确示范（LiteGraph）

LiteGraph 的 `DragAndScale.prototype.toCanvasContext` 长这样：

```js
// litegraph.js L5230
DragAndScale.prototype.toCanvasContext = function (ctx) {
    ctx.scale(this.scale, this.scale);
    ctx.translate(this.offset[0], this.offset[1]);
};
```

它**只做两件事**（缩放 + 平移），不 save、不 setTransform，
而且所有调用处都写成 `ctx.save(); this.ds.toCanvasContext(ctx); ... ctx.restore();`
—— 外层的 `save/restore` 正好能包住你加的 `scale`。

所以在这里注入最干净：

```js
(function installHiDPI() {
  const origToCtx = canvas.ds.toCanvasContext;
  canvas.ds.toCanvasContext = function (ctx) {
    const dpr = currentDPR();          // 实时取，见 §六
    if (dpr !== 1) ctx.scale(dpr, dpr);
    return origToCtx.call(this, ctx);
  };
})();
```

最终变换链变成：`dpr × scale(逻辑缩放) × translate(offset)` —— 正是想要的。

**通用判断法**：去找框架里那个「一行都干不了别的」的变换函数。
它通常叫 `toCanvasContext` / `applyTransform` / `resetTransform` / `setViewportTransform`。
只要是「不 save、不 setTransform、调用处被 save/restore 包着」，就是好落点。

---

## 四、其他会让画面糊的因素

### 4.1 位图开太大（上限保护）

dpr 异常大（某些虚拟显示器报 4）时位图会爆显存。夹一下：

```js
function currentDPR() {
  return Math.max(1, Math.min(3, window.devicePixelRatio || 1));
}
```

### 4.2 CSS 尺寸与位图尺寸不一致

如果只改了 `canvas.width` 却没设 `style.width`，CSS 尺寸会跟着位图变 → **元素变大、坐标错位**。

```js
host.width  = Math.round(w * dpr);
host.style.width = w + 'px';     // ← 这句不能少
```

### 4.3 缩放下文字重采样

`ctx.scale(0.5)` 时文字缩一半 —— 现代浏览器会按最终尺寸重新光栅化，**基本不糊**。
但如果框架是「先画到临时画布再整体缩小贴回来」，就会糊。
判断：放大到 200% 看，如果文字反而更虚，就是这个原因 → 改为按缩放后字号直接绘制。

### 4.4 深色底上的 ClearType 彩边

Canvas 2D 的 `fillText` 在深色背景上可能出现红蓝色边。
这不是"糊"但看起来脏。缓解：给深色底上的文字用半透明白而不是纯白，或改用 SVG / DOM 文本。

---

## 五、整块空白：别急着怀疑代码

### 5.1 先确认「到底画没画」

在页面里直接导出画布内容，绕开所有截图工具：

```js
const img = document.createElement('img');
img.id = 'cvshot';
img.src = canvas.toDataURL('image/png');
document.body.appendChild(img);
```

然后用 `--dump-dom` 抓 `<img id="cvshot" src="data:image/png;base64,...">` 存成文件看。

**这一步能立刻区分两种完全不同的情况**：
- 图片里有内容 → 代码没问题，是截图/渲染层的问题
- 图片里也空 → 真的没画出来，去查 `visible_nodes` / 坐标 / 异常

### 5.2 headless 截图截不到 canvas

`msedge --headless=new --disable-gpu --screenshot` 有时**不把 canvas 合成进截图**，
截出来全是背景色。**这是假象，不是 bug。**

其他假象来源：
- `--virtual-time-budget` 到点后页面冻结，rAF 停止
- 截图时机早于首帧绘制

所以：**能用 `toDataURL` 验证的，就不要用截图当证据。**

### 5.3 画到了离屏 canvas 上

有些框架把内容画在离屏 canvas（`bgcanvas`）再贴回主 canvas。
如果贴回的顺序错了（先贴、后画），主画布上就只有背景。

检查：

```js
canvas.bgcanvas === canvas.canvas   // true = 同体，正常
```

LiteGraph 0.7.18 里 `bgcanvas` 是独立创建的（`document.createElement("canvas")`），
但 `this.ctx` 在构造时指向**主 canvas**（L5668），所以绘制本身没问题。
只有在 `this.ctx` 为 null 时 L7857 才会退化成 `bgcanvas.getContext("2d")` —— 如果真走到那一步，内容就全画到离屏层了。

---

## 六、dpr 要实时读，不能只读一次

用户把窗口从笔记本屏（125%）拖到外接屏（100%），dpr 会变。
模块加载时读一次常量，换屏后就错了。

```js
function currentDPR() {
  return Math.max(1, Math.min(3, window.devicePixelRatio || 1));
}
```

在所有用到 dpr 的地方**调用它**，而不是引用一个缓存常量。
配合 `ResizeObserver` / `matchMedia('(resolution: Xdppx)')` 变化时重设一次画布尺寸即可。

---

## 七、验证方法（必须做，否则等于没修）

### 7.1 用无头浏览器模拟缩放

```powershell
msedge --headless=new --disable-gpu `
  --force-device-scale-factor=1.5 `     # ← 模拟 150% 屏
  --window-size=1138,712 `
  --virtual-time-budget=6000 --dump-dom $url
```

两种参数都要跑：
- `--force-device-scale-factor=1`：常规路径不能坏
- `--force-device-scale-factor=1.5`：HiDPI 路径生效

### 7.2 读关键数值，别靠眼睛

在页面里把下面这些写到 `document.title`，用 `--dump-dom` 抓回来：

```js
canvas.width + 'x' + canvas.height     // 位图尺寸，应 = CSS × dpr
canvas.clientWidth                     // CSS 尺寸
window.devicePixelRatio                // 缩放比
ds.scale, ds.offset                    // 框架内部变换
dpr !== 1 ? canvas.width / cssWidth : 1  // 应等于 dpr
```

**判定标准**：`位图宽 / CSS 宽 === dpr`。不相等就是没修好。

### 7.3 视觉对比要「同倍率裁块放大」

1. 修复前后各截一张**相同缩放比例**的图
2. 裁同一块区域
3. 用**最近邻**放大 3~4 倍并排看

```powershell
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
```

不要用平滑插值放大，那会把两边的差异都抹掉。

---

## 八、这次的完整代码（可直接抄）

```js
/* --- 1. dpr 实时取 --- */
function currentDPR() {
  return Math.max(1, Math.min(3, window.devicePixelRatio || 1));
}

/* --- 2. 位图按物理像素开，CSS 保持逻辑像素 --- */
function fitCanvas() {
  var parent = host.parentElement || host;   // ★ 用父容器量可见区，别用 canvas 自己
  var w = parent.clientWidth  || parent.offsetWidth;
  var h = parent.clientHeight || parent.offsetHeight;
  if (!w || !h) return;

  var d = currentDPR();
  host.width  = Math.round(w * d);
  host.height = Math.round(h * d);
  host.style.width  = w + 'px';
  host.style.height = h + 'px';

  if (canvas && canvas.resize) canvas.resize(host.width, host.height);
}

/* --- 3. 在「只做变换」的那个函数里注入 dpr --- */
(function installHiDPI() {
  var orig = canvas.ds.toCanvasContext;
  canvas.ds.toCanvasContext = function (ctx) {
    var d = currentDPR();
    if (d !== 1) ctx.scale(d, d);
    return orig.call(this, ctx);
  };
})();
```

---

## 九、排查清单（照着勾）

- [ ] `window.devicePixelRatio` 是多少？>1 就必须处理
- [ ] `canvas.width / canvas.clientWidth === dpr`？不等就是没修
- [ ] `canvas.style.width` 设了吗？（只改位图不改 CSS 会变大变形）
- [ ] `ctx.scale(dpr)` 注入的位置，外层有 `save/restore` 吗？
- [ ] 那个位置内部有没有 `setTransform(1,0,0,1,0,0)`？（有就换位置）
- [ ] `toDataURL()` 导出的内容正常吗？（判断是"没画"还是"没截到"）
- [ ] dpr 是实时读的吗？（换屏后不能失效）
- [ ] `--force-device-scale-factor` 两种参数都测过且都正常？

---

## 十、同项目里的其他坑（GalgameStudio 画布）

排查「糊」时顺带挖出来的，一并记着：

| 症状 | 根因 | 修法 |
|---|---|---|
| 滚轮缩放焦点乱飞 | 外层容器和 canvas 各有一套 wheel 监听，缩两次；外层只改 scale 不动 offset | 框架模式下让外层监听直接 `return` |
| 节点拖不动 | 框架判断 `e.which == 1`，但**合成 MouseEvent 的 which 恒为 0** | 测试时在 window 捕获阶段补 `which` |
| 每次打开缩放比例都不同 | 首屏先用默认数据渲染并排布，真实数据到达时不再重排 | 数据换批（按 id 集合判断）就重排一次 |
| 画面右侧被裁 | 拿 canvas 自身尺寸当可见区，但它可能被内容撑宽 | 量父容器的 `clientWidth` |

**符号约定（LiteGraph）**：`offset` 是平移量原值，
`canvasPx = (graph + offset) * scale`，反算 `graph = canvasPx / scale - offset`。
`adjustMouseEvent` / `convertCanvasToOffset` / `convertOffsetToCanvas` / `ctx.translate(offset)` 四处必须一致。

---

*最后更新：2026-11-06 · 适用 LiteGraph 0.7.18，原理通用于所有 canvas 自绘界面*
