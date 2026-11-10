/*
 * GalEditor —— LiteGraph 封装层（交互内核来自 LiteGraph 0.7.18，MIT）
 *
 * 目标：只替换「画布这一层」，app.js 里的 nodes / links 数据结构原样保留。
 *   GalEditor.load(nodes, links)  把扁平结构灌进 LiteGraph
 *   GalEditor.dump()              读回来成同样的扁平结构
 *
 * 对外依赖：window.LiteGraph（vendor/litegraph.js）
 */
/**
 * LiteGraph 右键菜单中文化
 *
 * 菜单项的英文是硬编码在 litegraph.js 里的（getNodeMenuOptions / getCanvasMenuOptions
 * 里写死的字面量），而且子菜单（Mode / Colors / Shapes）是点开时才动态生成的。
 * 所以不去改第三方库，而是包住最底层的 `ContextMenu.prototype.addItem` ——
 * 所有菜单项（顶层 + 任意层子菜单 + 添加节点列表）最终都从这个口子加进去。
 * 好处：以后换了 litegraph 版本也不用重新找字符串。
 */
(function () {
  var ZH = {
    // 节点右键
    'Inputs': '输入', 'Outputs': '输出', 'Properties': '属性',
    'Title': '标题', 'Mode': '模式', 'Resize': '调整大小',
    'Collapse': '折叠', 'Pin': '固定', 'Colors': '颜色',
    'Shapes': '形状', 'Clone': '克隆', 'Remove': '删除',
    'Align Selected To': '对齐选中项到',
    'To Subgraph': '转成子图',
    // 画布右键
    'Add Node': '添加节点', 'Add Group': '添加分组',
    'Align': '对齐', 'Close subgraph': '关闭子图',
    // 分组右键
    'Color': '颜色', 'Font size': '字号',
    // Mode 子菜单（LiteGraph.NODE_MODES）
    'Always': '总是', 'On Event': '有事件时', 'Never': '从不', 'On Trigger': '被触发时',
    // Shapes 子菜单（LiteGraph.VALID_SHAPES）
    'default': '默认', 'box': '方形', 'round': '圆角', 'card': '卡片',
    // Colors 子菜单
    'black': '黑', 'red': '红', 'green': '绿', 'blue': '蓝',
    'pale green': '浅绿', 'brown': '棕', 'pale blue': '浅蓝',
    'yellow': '黄', 'orange': '橙', 'purple': '紫', 'cyan': '青'
  };

  function install() {
    var L = window.LiteGraph;
    if (!L || !L.ContextMenu || !L.ContextMenu.prototype) return false;
    var proto = L.ContextMenu.prototype;
    if (proto.__zhPatched) return true;
    var orig = proto.addItem;
    if (typeof orig !== 'function') return false;

    proto.addItem = function (name, value, options) {
      if (typeof name === 'string' && Object.prototype.hasOwnProperty.call(ZH, name)) {
        name = ZH[name];
      }
      return orig.call(this, name, value, options);
    };
    proto.__zhPatched = true;
    return true;
  }

  if (!install()) {
    // litegraph 还没就绪就等一拍（正常情况脚本顺序已经保证，这里只是保险）
    var tries = 0;
    var timer = setInterval(function () {
      if (install() || ++tries > 100) clearInterval(timer);
    }, 30);
  }
})();

window.GalEditor = (function () {
  'use strict';
  var L = window.LiteGraph;

  var C = {
    bg: '#fafbfc',
    grid: '#eef1f5',
    card: '#ffffff',
    line: '#e6e9ef',
    lineSel: '#4f46e5',
    txt: '#1f2430',
    txt3: '#9aa2b1',
    soft: '#f7f8fa',
    thumb1: '#2b3a55',
    thumb2: '#16202f'
  };
  /* 后端契约里 choice 是 2~4 个选项：画布固定留 4 个出口，
     没连线的出口只是空槽（保存时由 app.js 按 to 过滤掉），所以 2 选项、3 选项、
     4 选项都能画出来，不需要为每种情况换一套节点类。 */
  var KIND = {
    start: { label: '序章', dot: '#2563eb', outs: 1, hasIn: false },
    scene: { label: '场景', dot: '#059669', outs: 1, hasIn: true },
    choice: { label: '分支', dot: '#d97706', outs: 4, hasIn: true, outLabels: ['A', 'B', 'C', 'D'] },
    ending: { label: '结局', dot: '#dc2626', outs: 0, hasIn: true }
  };
  var CHOICE_MIN = 2;
  var CHOICE_MAX = KIND.choice.outs;

  var graph = null;
  var canvas = null;
  var host = null;
  var api = { onSelect: null, onChange: null, onDouble: null };
  var uid = 0;
  var loading = false;

  function rr(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function makeGrid(size, color) {
    var c = document.createElement('canvas');
    c.width = c.height = size;
    var g = c.getContext('2d');
    g.strokeStyle = color;
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(0.5, 0); g.lineTo(0.5, size); g.stroke();
    g.beginPath(); g.moveTo(0, 0.5); g.lineTo(size, 0.5); g.stroke();
    return c.toDataURL();
  }

    /* ---------------- 节点：整张卡片自绘 ---------------- */
  /* 卡片尺寸只定义「画布上的逻辑尺寸」，缩放交给 LiteGraph；
     卡片内部的每一块位置都按 w/h 现算，改宽度不会错位。 */
  var NODE_W = 352;
  var NODE_H = 216;
  var PAD = 12;
  var HEAD_H = 36;

  /* 图片缓存：一张图只建一个 Image，加载完主动重绘一次画布。
     背景 / 立绘都按原始宽高比等比塞进预览框 —— 横图(16:9)、竖图(768×1344)
     或以后任何尺寸都不用改代码。 */
  var IMGS = {};

  function assetURLOf(rel) {
    if (!rel) return '';
    if (/^(data:|blob:|https?:|file:)/i.test(rel)) return rel;
    try {
      if (api.assetURL) {
        var u = api.assetURL(rel);
        if (u) return u;
      }
    } catch (e) {}
    return '';
  }

  function imgOf(rel) {
    var url = assetURLOf(rel);
    if (!url) return null;
    var e = IMGS[url];
    if (!e) {
      e = IMGS[url] = { img: new Image(), ok: false };
      e.img.onload = function () { e.ok = true; if (canvas) canvas.setDirty(true, false); };
      e.img.onerror = function () { e.ok = false; };
      e.img.src = url;
    }
    return e.ok ? e.img : null;
  }

  function rgba(hex, a) {
    var m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ''));
    if (!m) return 'rgba(120,130,150,' + a + ')';
    var v = parseInt(m[1], 16);
    return 'rgba(' + ((v >> 16) & 255) + ',' + ((v >> 8) & 255) + ',' + (v & 255) + ',' + a + ')';
  }

  function fillRound(ctx, x, y, w, h, r, color) {
    if (w <= 0 || h <= 0) return;
    ctx.fillStyle = color;
    rr(ctx, x, y, w, h, Math.min(r, w / 2, h / 2));
    ctx.fill();
  }

  /** 等比塞进矩形：crop=false 完整显示（立绘，不裁头），crop=true 铺满（背景） */
  function drawFit(ctx, img, x, y, w, h, r, crop) {
    if (!img || w <= 0 || h <= 0) return;
    var iw = img.naturalWidth || img.width || 0;
    var ih = img.naturalHeight || img.height || 0;
    if (!iw || !ih) return;
    var s = crop ? Math.max(w / iw, h / ih) : Math.min(w / iw, h / ih);
    var dw = iw * s, dh = ih * s;
    ctx.save();
    rr(ctx, x, y, w, h, Math.min(r, w / 2, h / 2));
    ctx.clip();
    ctx.drawImage(img, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
    ctx.restore();
  }

  function ellipsis(ctx, text, maxW) {
    var s = String(text == null ? '' : text);
    if (!s || ctx.measureText(s).width <= maxW) return s;
    while (s.length > 1 && ctx.measureText(s + '…').width > maxW) s = s.slice(0, -1);
    return s + '…';
  }

  /** 按可用宽度折行，最多 maxLines 行，截断时最后一行补省略号 */
  function wrapText(ctx, text, maxW, maxLines) {
    var s = String(text == null ? '' : text);
    var out = [], line = '', i, ch, probe;
    for (i = 0; i < s.length; i++) {
      ch = s.charAt(i);
      if (ch === '\n') {
        out.push(line);
        line = '';
        if (out.length >= maxLines) break;
        continue;
      }
      probe = line + ch;
      if (line && ctx.measureText(probe).width > maxW) {
        out.push(line);
        line = ch;
        if (out.length >= maxLines) break;
      } else {
        line = probe;
      }
    }
    if (out.length < maxLines && line) out.push(line);
    if (!out.length) out.push('');
    var used = out.join('').replace(/…$/, '').length;
    if (used < s.replace(/\n/g, '').length) {
      out[maxLines - 1] = ellipsis(ctx, out[maxLines - 1] + '…', maxW).replace(/…+$/, '') + '…';
    }
    return out;
  }

  /** 出口在卡片内的 y：直接用 LiteGraph 自己的槽位算法，不手算 */
  function outSlotY(node, i) {
    try {
      if (typeof node.getConnectionPos === 'function') {
        var pos = node.getConnectionPos(false, i);
        if (pos && typeof pos[1] === 'number') return pos[1] - node.pos[1];
      }
    } catch (e) {}
    return null;
  }

  var END_TEXT = { true: '真结局', good: '好结局', bad: '坏结局', alt: '分歧结局' };
  var BG_LABEL = { rainy: '雨夜', dusk: '黄昏', white: '清晨' };

  function drawCard(node, ctx, graphcanvas, kind, meta) {
    var w = node.size[0], h = node.size[1];
    var sel = node.flags && node.flags.selected;
    var p = node.properties;
    var z = (graphcanvas && graphcanvas.ds) ? graphcanvas.ds.scale : 1;

    /* 全图总览只保留可辨识的色块与边框：不排版文字、不创建阴影/渐变，
       让 800~1000 节点在最小缩放下的绘制成本近似随可见节点数线性增长。
       ★ 原来底色用 C.card（近白），在浅色画布上等于没画 —— 缩到 10% 时整张图
       只剩一排细竖线，看不出哪段是分支、哪段是结局。现在按类型上色，
       全图一眼能看出「主干 + 分叉点 + 结局」的骨相。 */
    if (z < 0.35) {
      ctx.fillStyle = rgba(meta.dot, 0.55);
      ctx.fillRect(0, 0, w, h);
      // 描边要按缩放反算，否则 10% 下 2px 描边在屏幕上只有 0.2px，等于看不见
      var lw = Math.min(18, 2 / Math.max(z, 0.08));
      ctx.strokeStyle = rgba(meta.dot, 0.95);
      ctx.lineWidth = lw;
      ctx.strokeRect(lw / 2, lw / 2, w - lw, h - lw);
      if (sel) {
        ctx.strokeStyle = C.lineSel;
        ctx.lineWidth = lw * 1.6;
        ctx.strokeRect(lw * 0.8, lw * 0.8, w - lw * 1.6, h - lw * 1.6);
      }
      return;
    }
    var lod = z < 0.55;

    /* 布局：右侧立绘列 = 卡片宽度的 40%（并有上下限），左栏吃掉剩下的宽度。
       所有坐标都从 w/h 推出来，改卡片尺寸或改窗口都不用重排代码。 */
    var picW = Math.round(Math.max(92, Math.min(190, w * 0.40)));
    if (picW > w - 150) picW = Math.max(80, w - 150);
    var picX = w - PAD - picW;
    var picY = HEAD_H + 6;
    var picH = h - picY - PAD;
    var leftX = PAD + 6;
    var leftW = picX - leftX - 10;
    var metaY = h - 16;
    var chRel = p.charAsset || '';
    var chImg = lod ? null : imgOf(chRel);
    var bgImg = lod ? null : imgOf(p.bgAsset);
    var bgRatio = 9 / 16;
    if (bgImg && bgImg.naturalWidth && bgImg.naturalHeight) {
      bgRatio = bgImg.naturalHeight / bgImg.naturalWidth;
    }
    var bgH = Math.round(leftW * Math.max(0.34, Math.min(0.80, bgRatio)));
    var maxBgH = Math.max(34, (metaY - 30) - picY);
    if (bgH > maxBgH) bgH = maxBgH;

    // 卡片底板
    ctx.save();
    ctx.shadowColor = 'rgba(16,24,40,.10)';
    ctx.shadowBlur = lod ? 0 : (sel ? 18 : 10);
    ctx.shadowOffsetY = lod ? 0 : (sel ? 6 : 3);
    ctx.fillStyle = C.card;
    rr(ctx, 0, 0, w, h, 12);
    ctx.fill();
    ctx.restore();

    ctx.strokeStyle = sel ? C.lineSel : C.line;
    ctx.lineWidth = sel ? 2 : 1;
    rr(ctx, 0.5, 0.5, w - 1, h - 1, 12);
    ctx.stroke();

    // 左侧类型色条：四种节点一眼能分
    ctx.save();
    rr(ctx, 0, 0, w, h, 12);
    ctx.clip();
    ctx.fillStyle = meta.dot;
    ctx.fillRect(0, 0, 4, h);
    ctx.restore();

    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';

    // 序号码
    ctx.font = '11px "Microsoft YaHei", sans-serif';
    ctx.fillStyle = C.txt3;
    ctx.fillText(p.idx || '00', leftX, 19);

    // 类型药丸（放在标题前面：右上角要留给分支的 A/B/C/D 出口角标，别互相压住）
    ctx.font = '10.5px "Microsoft YaHei", sans-serif';
    var pillW = Math.ceil(ctx.measureText(meta.label).width) + 16;
    var pillX = leftX + 20;
    fillRound(ctx, pillX, 9, pillW, 18, 9, rgba(meta.dot, 0.12));
    ctx.fillStyle = meta.dot;
    ctx.fillText(meta.label, pillX + 8, 19);

    // 标题
    ctx.font = '13px "Microsoft YaHei", sans-serif';
    ctx.fillStyle = C.txt;
    var titleX = pillX + pillW + 10;
    var titleMax = (w - PAD - 6) - titleX;
    ctx.fillText(ellipsis(ctx, p.title || '未命名', titleMax), titleX, 19);

    /* 立绘预览：卡片右侧独立预览列（原来是 90×56 的小方块） */
    fillRound(ctx, picX, picY, picW, picH, 9, '#f4f6fa');
    if (chImg) {
      drawFit(ctx, chImg, picX, picY, picW, picH, 9, false);
    } else if (!lod) {
      var cx = picX + picW / 2;
      ctx.fillStyle = '#e3e7ef';
      ctx.beginPath();
      ctx.arc(cx, picY + picH * 0.30, picW * 0.15, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(cx - picW * 0.28, picY + picH * 0.90);
      ctx.quadraticCurveTo(cx, picY + picH * 0.46, cx + picW * 0.28, picY + picH * 0.90);
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = '#a3abbd';
      ctx.textAlign = 'center';
      ctx.font = '10.5px "Microsoft YaHei", sans-serif';
      ctx.fillText(chRel ? '载入中…' : '无立绘', cx, picY + picH * 0.72);
      ctx.textAlign = 'left';
    }
    ctx.strokeStyle = 'rgba(16,24,40,.08)';
    ctx.lineWidth = 1;
    rr(ctx, picX + 0.5, picY + 0.5, picW - 1, picH - 1, 9);
    ctx.stroke();

    if (!lod) {
      var tag = p.whoName || p.who || (kind === 'ending' ? '收尾' : '立绘');
      ctx.font = '10px "Microsoft YaHei", sans-serif';
      var tagW = Math.min(picW - 12, Math.ceil(ctx.measureText(tag).width) + 12);
      fillRound(ctx, picX + 6, picY + picH - 21, tagW, 15, 5, 'rgba(10,14,24,.46)');
      ctx.fillStyle = 'rgba(255,255,255,.94)';
      ctx.fillText(ellipsis(ctx, tag, tagW - 12), picX + 12, picY + picH - 13);
    }

    // 背景缩略图（铺满，等卡片内左栏宽度）
    if (bgImg) {
      drawFit(ctx, bgImg, leftX, picY, leftW, bgH, 7, true);
    } else {
      var grad = ctx.createLinearGradient(leftX, picY, leftX + leftW, picY + bgH);
      grad.addColorStop(0, C.thumb1);
      grad.addColorStop(1, C.thumb2);
      fillRound(ctx, leftX, picY, leftW, bgH, 7, grad);
    }
    if (!lod) {
      var lb = 'bg ' + (BG_LABEL[p.bg] || p.bg || '未标注');
      ctx.font = '10px "Microsoft YaHei", sans-serif';
      var lbW = Math.min(leftW - 10, Math.ceil(ctx.measureText(lb).width) + 10);
      fillRound(ctx, leftX + 5, picY + bgH - 19, lbW, 15, 5, 'rgba(8,12,22,.42)');
      ctx.fillStyle = 'rgba(255,255,255,.9)';
      ctx.fillText(ellipsis(ctx, lb, lbW - 10), leftX + 10, picY + bgH - 11);
    }

    // 台词：按左栏宽度折行，最多两行
    ctx.fillStyle = p.text ? C.txt : C.txt3;
    ctx.font = '12px "Microsoft YaHei", sans-serif';
    var body = wrapText(ctx, p.text || '双击写台词…', leftW, 2);
    var lineTop = picY + bgH + 13;
    for (var i = 0; i < body.length; i++) {
      if (lineTop + i * 15 > metaY - 7) break;
      ctx.fillText(body[i], leftX, lineTop + i * 15);
    }

    if (lod) return;

    // 底部：时长 / 结局类型 / 分支性质 + 波形
    ctx.font = '10.5px "Microsoft YaHei", sans-serif';
    ctx.fillStyle = C.txt3;
    var durTxt = Number(p.dur) > 0 ? Number(p.dur).toFixed(1) + 's' : '待生成';
    var metaTxt;
    if (kind === 'ending') {
      metaTxt = '◷ ' + (END_TEXT[p.endKind] || '结局');
    } else if (kind === 'choice') {
      metaTxt = '◷ ' + durTxt + ' · ' + (p.branchKind === 'flavor' ? '气氛分支' : '必要分支');
    } else {
      metaTxt = '◷ ' + durTxt;
    }
    ctx.fillText(metaTxt, leftX, metaY);

    var wx = leftX + Math.ceil(ctx.measureText(metaTxt).width) + 10;
    var bars = Math.max(0, Math.min(16, Math.floor((picX - 10 - wx) / 4)));
    for (var b = 0; b < bars; b++) {
      var bh = 3 + ((b * 37) % 9);
      ctx.fillStyle = '#d8dde6';
      ctx.fillRect(wx + b * 4, metaY - bh / 2, 2, bh);
    }

    // 分支的 4 个出口标 A/B/C/D；空出口（没连线）用灰字虚线感，一眼看出哪些在用
    if (kind === 'choice' && meta.outLabels) {
      var outs = node.outputs || [];
      for (var oi = 0; oi < outs.length; oi++) {
        var oy = outSlotY(node, oi);
        if (oy == null) continue;
        var lbl = meta.outLabels[oi] || String.fromCharCode(65 + oi);
        var used = !!(((outs[oi] || {}).links) || []).length;
        ctx.font = 'bold 10px "Microsoft YaHei", sans-serif';
        var bx = w - 32;
        fillRound(ctx, bx, oy - 8, 16, 16, 8,
          used ? '#ffffff' : 'rgba(255,255,255,.78)');
        ctx.strokeStyle = used ? rgba(meta.dot, 0.7) : 'rgba(158,166,182,.6)';
        ctx.lineWidth = 1.2;
        rr(ctx, bx + 0.5, oy - 7.5, 15, 15, 7.5);
        ctx.stroke();
        ctx.fillStyle = used ? meta.dot : '#a2aabb';
        ctx.textAlign = 'center';
        ctx.fillText(lbl, bx + 8, oy);
      }
      ctx.textAlign = 'left';
    }
  }

  function makeNodeType(kind) {
    var meta = KIND[kind];
    function Node() {
      this.color = 'rgba(255,255,255,0)';   // 标题栏透明（内容自己画）
      this.boxcolor = 'transparent';
      this.shape = L.ROUND_SHAPE;
      this.title = '';
      if (meta.hasIn) this.addInput('', 'gal');
      for (var i = 0; i < meta.outs; i++) this.addOutput('', 'gal');
      this.properties = {
        galId: '', kind: kind, idx: '', title: '',
        text: '', bg: '雨夜', endKind: 'true', dur: 0, who: '', whoName: '',
        branchKind: 'key', bgAsset: '', charAsset: ''
      };
      this.size = [NODE_W, NODE_H];
    }
    Node.title = meta.label;
    Node.prototype.onGetTitle = function () { return ''; };
    // ★ LiteGraph 画标题用的是 getTitle()（不是 onGetTitle）：
    //   不覆盖它就按 constructor.title 在卡片上方画一行幽灵标题，卡片顶部会露半个标签。
    Node.prototype.getTitle = function () { return ''; };
    Node.prototype.onDrawBackground = function (ctx, graphcanvas) {
      drawCard(this, ctx, graphcanvas, kind, meta);
    };

    Node.prototype.onDblClick = function () {
      if (api.onDouble) api.onDouble(this);
    };

    return Node;
  }

  /* ---------------- 内部：扁平 <-> LiteGraph ---------------- */
  /** LiteGraph 内部 id → 项目节点 id（galId）。
   *  ★ 不能写成 'n' + liteId：graph.clear() 会把 last_node_id 归零，单章视图只装本章节点，
   *    内部 id 1 对应的是本章第一个节点（比如 n44），拼出来却是 'n1' ——
   *    p_0492… 的 start 长出 27 条出边、c002 长出 26 个选项就是这么来的。 */
  function galIdOf(liteId) {
    var nd = graph && graph.getNodeById(liteId);
    return (nd && nd.properties && nd.properties.galId) || ('n' + liteId);
  }

  function linkTarget(slot) {
    var target = null;
    ((slot && slot.links) || []).forEach(function (lid) {
      var lk = graph.links[lid];
      if (lk && !target) target = lk;
    });
    return target ? galIdOf(target.target_id) : '';
  }

  function toFlat(node) {
    var p = node.properties;
    var kind = p.kind;
    var out = {
      id: p.galId || ('n' + node.id),
      type: kind,
      idx: p.idx || '',
      title: p.title || '',
      x: Math.round(node.pos[0]),
      y: Math.round(node.pos[1]),
      who: p.who || '',
      whoName: p.whoName || '',
      text: p.text || '',
      bg: p.bg || '雨夜',
      voice: p.dur || 0,
      branchKind: p.branchKind || '',
      bgAsset: p.bgAsset || '',
      charAsset: p.charAsset || ''
    };
    var outs = node.outputs || [];
    if (kind === 'choice') {
      out.choices = [];
      for (var k = 0; k < KIND.choice.outs; k++) {
        var to = linkTarget(outs[k]);
        var item = {
          label: (p.choiceLabels && p.choiceLabels[k]) || ('选项 ' + String.fromCharCode(65 + k)),
          to: to
        };
        // 没连线的槽位只是画布上的空出口：显式标 placeholder，保存/试玩据此丢弃，不靠文字匹配。
        if (!to) item.placeholder = true;
        out.choices.push(item);
      }
    } else if (kind === 'start' || kind === 'scene') {
      // start/scene 也把唯一出口读回来，nodes[].choices 才能当唯一真相（试玩、保存都读它）。
      var next = linkTarget(outs[0]);
      out.choices = next ? [{ label: '继续', to: next }] : [];
    }
    if (kind === 'ending') out.endKind = p.endKind || 'true';
    return out;
  }

  function setNode(node, n) {
    var p = node.properties;
    p.galId = n.id;
    p.kind = n.type;
    p.idx = n.idx || '';
    p.title = n.title || '';
    p.text = n.text || '';
    p.bg = n.bg || '雨夜';
    p.who = n.who || '';
    p.whoName = n.whoName || '';
    p.dur = n.voice || 0;
    p.bgAsset = n.bgAsset || '';
    p.charAsset = n.charAsset || '';
    if (n.type === 'ending') p.endKind = n.endKind || 'true';
    if (n.type === 'choice') {
      p.branchKind = n.branchKind === 'flavor' ? 'flavor' : 'key';
      var labels = (n.choices || []).map(function (c) { return c.label; });
      // 补齐到 4 个：2~4 个选项都能直接显示，空槽给个占位名
      for (var k = labels.length; k < KIND.choice.outs; k++) {
        labels.push('选项 ' + String.fromCharCode(65 + k));
      }
      p.choiceLabels = labels;
    }
    node.pos = [n.x || 0, n.y || 0];
  }

  /* ---------------- 对外 API ---------------- */
  function init(canvasEl) {
    host = canvasEl;
    graph = new LGraph();

    L.NODE_TITLE_COLOR = C.txt3;
    L.NODE_SELECTED_TITLE_COLOR = C.lineSel;
    L.NODE_TEXT_COLOR = C.txt;
    L.NODE_DEFAULT_COLOR = C.soft;
    L.NODE_DEFAULT_BGCOLOR = C.card;
    L.LINK_COLOR = '#c2c8d4';
    L.EVENT_LINK_COLOR = C.lineSel;
    L.CONNECTING_LINK_COLOR = C.lineSel;
    L.NODE_DEFAULT_BOXCOLOR = 'transparent';
    if (L.LGraphCanvas && L.LGraphCanvas.link_type_colors) {
      L.LGraphCanvas.link_type_colors['gal'] = '#b3bac7';
    }

    /* ★ 汇合节点（入度 ≥2）不能只有一个输入口：LiteGraph 往已占用的输入口 connect 时
       会先 disconnectInput 把旧线拆掉（vendor/litegraph.js「if there is something already
       plugged there, disconnect」）。菱形的两条腿都连向同一个汇合场景 → 装载时第一条腿的
       出边被静默拆掉 → dump 出来就是「scene 出边 0」（p_38052… 的 n13、p_ff87… 的 n15 都是这样）。
       这里在已占用时给目标节点补一个输入口再连，装载和手动连线都走这条路。
       （vendor 文件不许改，所以包一层 prototype.connect，只对 galgame/* 节点生效。
       必须在 registerNodeType 之前装：注册时 LiteGraph 会把 LGraphNode.prototype 的方法
       拷到各节点类上，之后再改原型不起作用。） */
    var NodeProto = (L.LGraphNode || window.LGraphNode).prototype;
    if (!NodeProto.__galMultiInput) {
      var connect = NodeProto.connect;
      NodeProto.connect = function (slot, target, targetSlot) {
        if (target && typeof target === 'object' && /^galgame\//.test(target.type || '')
            && typeof targetSlot === 'number' && target.inputs && target.inputs[targetSlot]
            && target.inputs[targetSlot].link != null) {
          var free = -1;
          for (var i = 0; i < target.inputs.length; i++) {
            if (target.inputs[i].link == null) { free = i; break; }
          }
          if (free < 0) { target.addInput('', 'gal'); free = target.inputs.length - 1; }
          targetSlot = free;
        }
        return connect.call(this, slot, target, targetSlot);
      };
      NodeProto.__galMultiInput = true;
    }

    ['start', 'scene', 'choice', 'ending'].forEach(function (k) {
      L.registerNodeType('galgame/' + k, makeNodeType(k));
    });

    canvas = new LGraphCanvas(host, graph);

    /* ★ 关掉 LiteGraph 的「双击画布空白弹搜索框」（allow_searchbox）。
       这个功能在这个界面里是坏的 —— 实测双击画布空白稳定复现：
         · 它把 <div class="litegraph litesearchbox"> 直接 append 到 document.body 末尾，
           而 .litesearchbox 的 CSS 只给了 position:absolute、没给 left/top，
           于是框子按静态位置落在页面最下方（看起来像"画布下角冒出个 Search"）；
         · 它还会把 body.style.overflow 设成 "hidden"，整页不能滚、布局宽度重算，
           就是你看到的「挡住画布 + 挤压界面」。
       litegraph.js 是 vendor 文件，不许改，所以从这边把开关关掉。
       加节点走工具栏的「+ 节点」按钮就够了。 */
    canvas.allow_searchbox = false;

    /* ★ HiDPI：LiteGraph 0.7.18 里一个 devicePixelRatio 都没有 —— 位图缓存按 CSS 尺寸开，
       在 150% 缩放的屏上被浏览器放大 1.5 倍铺到物理像素，节点文字/圆角/连线全发虚。
       这里补两件事：
         1) 位图按 dpr 放大（CSS 尺寸仍用逻辑尺寸，所以鼠标换算不受影响）
         2) 每次应用画布变换时补一个 ctx.scale(dpr) —— toCanvasContext 只做
            scale(scale)+translate(offset) 且调用处都包着 save/restore，是最干净的注入点 */
    (function installHiDPI() {
      var origToCtx = canvas.ds.toCanvasContext;
      canvas.ds.toCanvasContext = function (ctx) {
        var r = currentDPR();
        if (r !== 1) ctx.scale(r, r);
        return origToCtx.call(this, ctx);
      };
    })();

    canvas.clear_background_color = C.bg;    canvas.background_image = makeGrid(24, C.grid);
    canvas.render_connection_arrows = false;
    canvas.render_canvas_border = false;
    canvas.render_connections_shadows = false;
    canvas.links_render_mode = L.SPLINE_LINK;
    canvas.default_link_color = '#b3bac7';
    canvas.show_info = false;
    canvas.ds.max_scale = 2.5;
    canvas.ds.min_scale = 0.25;

    /* 滚轮缩放：以鼠标所在点为中心。
       LiteGraph 原版用 e.clientX（屏幕坐标）算中心，但 canvas 不在页面 (0,0)，
       偏移量会把焦点带偏。这里改成 canvas 内坐标重算。 */
    canvas.processMouseWheel = function (e) {
      if (!this.graph) return;
      // 统一增量：标准 wheel 事件用 deltaY（正=向下=缩小）；
      // 老式 mousewheel 事件只有 wheelDeltaY（正=向上=放大），符号相反。
      var down;
      if (typeof e.deltaY === 'number' && e.deltaY !== 0) {
        down = e.deltaY > 0;
      } else if (typeof e.wheelDeltaY === 'number' && e.wheelDeltaY !== 0) {
        down = e.wheelDeltaY < 0;
      } else if (typeof e.wheelDelta === 'number' && e.wheelDelta !== 0) {
        down = e.wheelDelta < 0;
      } else {
        return;
      }

      var rect = this.canvas.getBoundingClientRect();
      var cx = e.clientX - rect.left;
      var cy = e.clientY - rect.top;
      if (cx < 0 || cy < 0 || cx > rect.width || cy > rect.height) return;

      var s0 = this.ds.scale;
      var s1 = s0 * (down ? 1 / 1.12 : 1.12);
      var lim0 = this.ds.min_scale || 0.25, lim1 = this.ds.max_scale || 2.5;
      s1 = Math.max(lim0, Math.min(lim1, s1));
      userZoomed = true;          // 用户自己缩过了，之后别再自动拽视野
      if (Math.abs(s1 - s0) < 1e-6) { e.preventDefault(); return false; }

      // 变换关系：canvasPx = (graph + offset) * scale
      // 反解出鼠标下的图坐标，缩放后把它重新顶到鼠标下 → 焦点不动
      var gx = cx / s0 - this.ds.offset[0];
      var gy = cy / s0 - this.ds.offset[1];
      this.ds.scale = s1;
      this.ds.offset[0] = cx / s1 - gx;
      this.ds.offset[1] = cy / s1 - gy;
      this.setDirty(true, true);
      e.preventDefault();
      return false;
    };

    /* ★ 关键：LiteGraph 构造函数里已经 this.processMouseWheel.bind(this) 存进了
       _mousewheel_callback，只覆盖方法没用 —— 必须把监听也替换掉。
       现代 Edge/Chrome 只派发标准 wheel 事件，所以只绑这一个，避免重复触发。 */
    (function rebindWheel() {
      var oldCb = canvas._mousewheel_callback;
      if (oldCb) {
        canvas.canvas.removeEventListener('mousewheel', oldCb, false);
        canvas.canvas.removeEventListener('DOMMouseScroll', oldCb, false);
        canvas.canvas.removeEventListener('wheel', oldCb, false);
      }
      canvas._mousewheel_callback = function (e) { return canvas.processMouseWheel.call(canvas, e); };
      canvas.canvas.addEventListener('wheel', canvas._mousewheel_callback, { passive: false });
    })();

    // 选中 / 变化 回调
    var oldSelect = canvas.onNodeSelected;
    canvas.onNodeSelected = function (node) {
      if (oldSelect) oldSelect.call(canvas, node);
      if (api.onSelect) api.onSelect(node ? node.properties.galId : null);
    };
    canvas.onNodeDeselected = function () {
      if (api.onSelect) api.onSelect(null);
    };
    graph.onNodeAdded = function () {
      if (!loading && api.onChange) api.onChange();
    };
    graph.onConnectionChange = function () {
      if (!loading && api.onChange) api.onChange();
    };
    // 拖动结束后把新坐标同步回 app.js（否则保存时用的还是旧坐标）
    canvas.onNodeMoved = function (node) {
      if (loading) return;
      if (api.onMove) api.onMove(node ? node.properties.galId : null);
      else if (api.onChange) api.onChange();
    };

    fitCanvas();
    window.addEventListener('resize', scheduleAutoFit);
    if (window.ResizeObserver) {
      try {
        new ResizeObserver(scheduleAutoFit).observe(host.parentElement || host);
      } catch (e) {}
    }
    graph.start();
    return api;
  }

  /* 屏幕缩放比（实时取：窗口拖到不同 DPI 的屏幕上会变）。
     上限 3 是防止某些机器上 dpr 异常大导致位图爆显存。 */
  function currentDPR() {
    return Math.max(1, Math.min(3, window.devicePixelRatio || 1));
  }
  var DPR = currentDPR();

  /* 可见区域尺寸 = 父容器尺寸，不是 canvas 自己报的尺寸。
     踩过的坑：canvas 一旦被撑宽（clientWidth 1322），父容器实际只有 1138 宽，
     右半边被 overflow 裁掉；而 fit() 按 1322 算，算出「刚好放下」，
     实际画面右侧就被切掉、节点看着像飞到视口外。 */
  function boxSize() {
    var p = (host && host.parentElement) || host;
    if (!p) return { w: 0, h: 0 };
    return {
      w: p.clientWidth || p.offsetWidth || 0,
      h: p.clientHeight || p.offsetHeight || 0
    };
  }

  function fitCanvas() {
    if (!host) return;
    var s = boxSize();
    var w = s.w, h = s.h;
    if (!w || !h) return;
    // 位图开成物理像素，CSS 尺寸保持逻辑像素 —— 两者分离才不会糊
    var d = currentDPR();
    var bw = Math.round(w * d), bh = Math.round(h * d);
    host.width = bw;
    host.height = bh;
    host.style.width = w + 'px';
    host.style.height = h + 'px';
    if (canvas && typeof canvas.resize === 'function') canvas.resize(bw, bh);
  }

  /* 窗口/容器尺寸变了要重新适配视野。
     踩过的坑：只在 rAF 里 fit 一次 —— 那时窗口往往还没定尺寸，
     每次打开算出来的缩放都不一样（实测见过 62% / 77% / 109% / 127%）。
     这里用 ResizeObserver + 防抖，尺寸稳定后再 fit；
     但用户一旦自己缩放过，就不再自动拽他的视野。 */
  var userZoomed = false;
  var autoFitTimer = null;
  function scheduleAutoFit() {
    fitCanvas();
    if (userZoomed) return;
    clearTimeout(autoFitTimer);
    autoFitTimer = setTimeout(function () {
      if (!userZoomed) fit();
    }, 180);
  }

  /** 全量灌入（app.js 的 nodes/links）
   *  ★ 这里必须防御脏数据：任何一条连线对不上就跳过，不能让整张图加载失败。
   *    并且装完自检数量，丢节点/丢线要能被发现（以前是静默丢，画布看着正常少一个）。 */
  function load(nodes, links) {
    if (!graph) return;
    loading = true;
    graph.clear();
    uid = 0;
    var map = {};
    var added = 0, skippedNode = 0;

    (nodes || []).forEach(function (n) {
      try {
        var kind = KIND[n.type] ? n.type : 'scene';
        var node = L.createNode('galgame/' + kind);
        if (!node) { skippedNode++; return; }
        if (!node.outputs) node.outputs = [];
        if (!node.inputs) node.inputs = [];
        setNode(node, n);
        graph.add(node);
        map[n.id] = node;
        added++;
      } catch (e) {
        skippedNode++;
        console.warn('[GalEditor.load] 节点装载失败', n && n.id, e);
      }
    });

    var linked = 0, skippedLink = 0, missingEnd = [];
    (links || []).forEach(function (l) {
      var a = map[l.from], b = map[l.to];
      if (!a || !b) { skippedLink++; missingEnd.push(l.from + '->' + l.to); return; }
      var k = l.port || 0;
      if (!a.outputs || !a.outputs[k]) { skippedLink++; missingEnd.push(l.from + '[port' + k + ']'); return; }
      try { a.connect(k, b, 0); linked++; } catch (e) { skippedLink++; }
    });

    window.__galLoadDiag = {
      at: Date.now(),
      nodesIn: (nodes || []).length, nodesAdded: added, nodesSkipped: skippedNode,
      linksIn: (links || []).length, linksAdded: linked, linksSkipped: skippedLink,
      missing: missingEnd.slice(0, 10)
    };
    if (skippedNode || skippedLink) {
      console.warn('[GalEditor.load] 有数据没装上：', window.__galLoadDiag);
    }

    if (canvas) canvas.setDirty(true, true);
    loading = false;
  }

  /** 读回扁平结构 */
  function dump() {
    var nodes = [], links = [];
    if (!graph) return { nodes: nodes, links: links };
    graph._nodes.forEach(function (n) {
      nodes.push(toFlat(n));
    });
    graph._nodes.forEach(function (n) {
      (n.outputs || []).forEach(function (slot, k) {
        ((slot && slot.links) || []).forEach(function (lid) {
          var lk = graph.links[lid];
          if (!lk) return;
          links.push({ from: galIdOf(lk.origin_id), port: k, to: galIdOf(lk.target_id) });
        });
      });
    });
    return { nodes: nodes, links: links };
  }

  function addNode(type) {
    if (!graph) return null;
    var kind = KIND[type] ? type : 'scene';
    var node = L.createNode('galgame/' + kind);
    uid += 1;
    // 放在视口中间
    var center = [0, 0];
    if (canvas && canvas.ds) {
      var tl = canvas.ds.offset ? [-canvas.ds.offset[0], -canvas.ds.offset[1]] : [0, 0];
      var sc = canvas.ds.scale || 1;
      center = [(host.clientWidth / 2) / sc + tl[0], (host.clientHeight / 2) / sc + tl[1]];
    }
    node.pos = [Math.round(center[0] - NODE_W / 2), Math.round(center[1] - NODE_H / 2)];
    node.properties.galId = 'n' + Date.now().toString(36) + uid.toString(36);
    node.properties.idx = String(graph._nodes.length + 1).padStart(2, '0');
    node.properties.title = KIND[kind].label;
    node.properties.text = kind === 'choice' ? '选择哪一个？' : kind === 'ending' ? '故事结束。' : '在这里写台词……';
    if (kind === 'choice') {
      node.properties.choiceLabels = ['选项 A', '选项 B', '选项 C', '选项 D'];
    }
    graph.add(node);
    if (canvas) {
      canvas.setDirty(true, true);
      canvas.selectNode(node, true);
    }
    return node;
  }

  function deleteSelectedNodes() {
    // LiteGraph 自带 Delete 处理，这里只给外部一个入口
    if (graph && canvas) canvas.deleteSelectedNodes();
  }

  function fit() {
    if (!canvas || !graph || !graph._nodes.length) return;
    var s0 = boxSize();
    if (!s0.w || !s0.h) return;   // 容器还没尺寸（编排页 display:none），别乱设
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    graph._nodes.forEach(function (n) {
      minX = Math.min(minX, n.pos[0]);
      minY = Math.min(minY, n.pos[1]);
      maxX = Math.max(maxX, n.pos[0] + n.size[0]);
      maxY = Math.max(maxY, n.pos[1] + n.size[1]);
    });
    var pad = 60;
    var w = s0.w, h = s0.h;
    var gw = maxX - minX + pad * 2, gh = maxY - minY + pad * 2;
    var scale = Math.min(w / gw, h / gh, 1.2);
    /* ★ 缩放下限分两档（实测：LiteGraph 在 21% 时卡片内容整个不画，只剩一根边框线，
       等于白缩到看不清。而 45% 时标题/缩略图/台词都在）。
       - 单章（≤60 节点）：保底 0.40 —— 宁可能横向拖两下，也要看得见内容
       - 全图：保底 0.05 —— 那是看骨相的概览，510 节点 23444px 宽，5% 才刚好装进视口 */
    var floor = graph._nodes.length <= 60 ? 0.40 : 0.05;
    scale = Math.max(floor, Math.min(2.2, scale));

    window.__fitLog = {
      w: w, h: h, gw: Math.round(gw), gh: Math.round(gh),
      want: +(w / gw).toFixed(3) + ' / ' + +(h / gh).toFixed(3),
      scale: +scale.toFixed(4),
      floor: floor,
      minX: Math.round(minX), minY: Math.round(minY),
      maxX: Math.round(maxX), maxY: Math.round(maxY),
      n: graph._nodes.length
    };

    /* 自愈适配：算出 scale/offset 后，用 LiteGraph 自己的
       convertOffsetToCanvas 反验内容四角是否真的落在视口内。
       不在就继续缩 —— 这样即使尺寸/时序出岔子，也一定能看到全图。
       缩到 floor 就停手：再小下去卡片内容就不画了，不如让用户拖。
       ★ 真的放不下时（单章 16 个节点在 40% 下就是会超宽），**从左上角开始对齐**，
       不要居中 —— 居中会把章首那几节裁到画布外面，主人一进来就找不到故事从哪开始。 */
    var placeOffset = function (alignStart) {
      var offx = (w - gw * scale) / (2 * scale) - (minX - pad);
      var offy = (h - gh * scale) / (2 * scale) - (minY - pad);
      if (alignStart && gw * scale > w) offx = 40 / scale - (minX - pad);
      if (alignStart && gh * scale > h) offy = 40 / scale - (minY - pad);
      canvas.ds.offset = [offx, offy];
    };

    var ok = false;
    for (var iter = 0; iter < 8; iter++) {
      canvas.ds.scale = scale;
      placeOffset(false);
      var tl = canvas.ds.convertOffsetToCanvas([minX - pad, minY - pad]);
      var br = canvas.ds.convertOffsetToCanvas([maxX + pad, maxY + pad]);
      ok = tl[0] >= -2 && tl[1] >= -2 && br[0] <= w + 2 && br[1] <= h + 2;
      if (ok) break;
      if (scale * 0.82 < floor) break;
      scale *= 0.82;
    }
    canvas.ds.scale = scale;
    placeOffset(!ok);
    window.__fitLog.ok = ok;
    window.__fitLog.finalScale = +scale.toFixed(4);
    canvas.setDirty(true, true);
  }

  /** 设缩放：以视口中心为焦点，保证画面不跑偏（工具栏 +/- 用） */
  function setZoom(z) {
    if (!canvas || !canvas.ds) return;
    userZoomed = true;
    var s0 = canvas.ds.scale;
    var s1 = Math.max(0.25, Math.min(2.5, z));
    if (Math.abs(s1 - s0) < 1e-6) return;
    var w = host.clientWidth, h = host.clientHeight;
    var cx = w / 2, cy = h / 2;                       // 焦点 = 视口中心
    var gx = cx / s0 - canvas.ds.offset[0];           // 焦点处的图坐标（注意是 -offset）
    var gy = cy / s0 - canvas.ds.offset[1];
    canvas.ds.scale = s1;
    canvas.ds.offset[0] = cx / s1 - gx;               // 让同一个图点仍落在视口中心
    canvas.ds.offset[1] = cy / s1 - gy;
    canvas.setDirty(true, true);
  }
  function getZoom() {
    return (canvas && canvas.ds) ? canvas.ds.scale : 1;
  }
  function redraw() { if (canvas) canvas.setDirty(true, true); }

  function updateNode(id, data) {
    if (!graph || !data) return;
    var node = graph._nodes.filter(function (n) { return n.properties.galId === id; })[0];
    if (!node) return;
    var p = node.properties;
    if (data.title !== undefined) p.title = data.title;
    if (data.text !== undefined) p.text = data.text;
    if (data.who !== undefined) p.who = data.who;
    if (data.whoName !== undefined) p.whoName = data.whoName;
    if (data.bg !== undefined) p.bg = data.bg;
    if (data.voice !== undefined) p.dur = data.voice;
    if (data.endKind !== undefined) p.endKind = data.endKind;
    if (data.branchKind !== undefined) p.branchKind = data.branchKind;
    if (data.bgAsset !== undefined) p.bgAsset = data.bgAsset;
    if (data.charAsset !== undefined) p.charAsset = data.charAsset;
    if (data.choiceLabels) {
      var labels = data.choiceLabels.slice();
      for (var k = labels.length; k < KIND.choice.outs; k++) {
        labels.push('选项 ' + String.fromCharCode(65 + k));
      }
      p.choiceLabels = labels;
    }
    if (canvas) canvas.setDirty(true, false);
  }

  /** canvas 内坐标 → 图坐标（调试/测试用） */
  function screenToGraph(cx, cy) {
    if (!canvas || !canvas.ds) return null;
    var s = canvas.ds.scale, o = canvas.ds.offset;
    return { x: cx / s - o[0], y: cy / s - o[1] };
  }

  return {
    init: init,
    load: load,
    dump: dump,
    addNode: addNode,
    deleteSelectedNodes: deleteSelectedNodes,
    fit: fit,
    autoFit: scheduleAutoFit,      // 带防抖 + 尊重用户手动缩放的适配
    setZoom: setZoom,
    getZoom: getZoom,
    redraw: redraw,
    updateNode: updateNode,
    fitCanvas: fitCanvas,
    screenToGraph: screenToGraph,
    _canvas: function () { return canvas; },
    kindMeta: KIND,
    choiceMin: CHOICE_MIN,
    choiceMax: CHOICE_MAX,
    nodeWidth: NODE_W,
    nodeHeight: NODE_H,
    api: api
  };
})();
