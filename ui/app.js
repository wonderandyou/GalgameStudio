/* ============================================================
   奇迹工坊 · Galgame Studio — 交互逻辑（纯前端 Demo，无后端）
   ============================================================ */
(function () {
  'use strict';

  /* ---------------- 数据：节点图 ---------------- */
  /* 卡片宽度跟 graph-editor.js 的 NODE_W 保持一致（那边负责画，这边拿它算连线端点/排布） */
  const NODE_W = 352;
  const NODE_H = 216;
  const CHOICE_MAX = 4;      // 后端契约：分支 2~4 个选项
  const CHOICE_MIN = 2;
  const TYPE_META = {
    start:  { color: '#2563eb', label: '序章',  bg: 'linear-gradient(90deg,rgba(37,99,235,.13),rgba(37,99,235,.02))' },
    scene:  { color: '#059669', label: '场景',  bg: 'linear-gradient(90deg,rgba(5,150,105,.13),rgba(5,150,105,.02))' },
    choice: { color: '#d97706', label: '选择',  bg: 'linear-gradient(90deg,rgba(217,119,6,.14),rgba(217,119,6,.02))' },
    ending: { color: '#dc2626', label: '结局',  bg: 'linear-gradient(90deg,rgba(220,38,38,.13),rgba(220,38,38,.02))' }
  };

  let nodes = [
    { id: 'n1', type: 'start', idx: '01', title: '序章 · 雨', x: 40, y: 320,
      who: '', text: '十一点四十分，天台的门没锁。雨把整座城市洗成一片模糊的橘色。', bg: 'rainy', voice: 3.8 },
    { id: 'n2', type: 'scene', idx: '02', title: '天台 · 她先到了', x: 360, y: 320,
      who: '她', text: '你迟到了七分钟。我还以为……你不会来了。', bg: 'rainy', voice: 4.6 },
    { id: 'n3', type: 'choice', idx: '03', title: '两张车票', x: 680, y: 320,
      who: '她', text: '票我买好了。凌晨一点，最后一班。你要走吗？', bg: 'rainy', voice: 5.1,
      choices: [
        { label: '「走。现在就走。」', to: 'n4' },
        { label: '「……我们回去吧。」', to: 'n5' },
        { label: '（沉默，看着她的手）', to: 'n6' }
      ] },
    { id: 'n4', type: 'scene', idx: '04', title: '沉默的三十秒', x: 1010, y: 110,
      who: '她', text: '她没说话，只是把票塞进我手里。指尖是凉的，像淋了很久的雨。', bg: 'rainy', voice: 5.4 },
    { id: 'n5', type: 'scene', idx: '05', title: '楼梯间的回声', x: 1010, y: 330,
      who: '她', text: '……好。那我把票退了。她说得很轻，轻得像怕吵醒什么。', bg: 'dusk', voice: 4.9 },
    { id: 'n6', type: 'scene', idx: '06', title: '她摊开的手', x: 1010, y: 550,
      who: '她', text: '她的手心里只有一张票。另一张，从来就不存在。', bg: 'rainy', voice: 4.2 },
    { id: 'n7', type: 'ending', idx: '07', title: '真结局 · 雨停之前', x: 1330, y: 110,
      who: '', text: '凌晨一点，站台的灯一盏盏灭下去。我们谁都没有回头。', bg: 'white', voice: 0, endKind: 'true' },
    { id: 'n8', type: 'ending', idx: '08', title: '坏结局 · 留在原地', x: 1330, y: 330,
      who: '', text: '雨一直下到天亮。第二天，她的座位空着，桌角放着那张退掉的车票。', bg: 'dusk', voice: 0, endKind: 'bad' },
    { id: 'n9', type: 'ending', idx: '09', title: '隐藏结局 · 一张票', x: 1330, y: 550,
      who: '', text: '她笑了。她说：我早就知道你不会走。所以，我只买了一张。', bg: 'rainy', voice: 0, endKind: 'alt' }
  ];

  let links = [
    { from: 'n1', port: 0, to: 'n2' },
    { from: 'n2', port: 0, to: 'n3' },
    { from: 'n3', port: 0, to: 'n4' },
    { from: 'n3', port: 1, to: 'n5' },
    { from: 'n3', port: 2, to: 'n6' },
    { from: 'n4', port: 0, to: 'n7' },
    { from: 'n5', port: 0, to: 'n8' },
    { from: 'n6', port: 0, to: 'n9' }
  ];

  const BG_GRAD = {
    rainy: 'radial-gradient(90% 60% at 50% 8%, rgba(86,116,200,.55), transparent 62%), linear-gradient(180deg,#20243c 0%,#171326 60%,#0d0b14 100%)',
    dusk:  'radial-gradient(80% 55% at 70% 15%, rgba(255,170,90,.55), transparent 62%), linear-gradient(180deg,#3a2438 0%,#241629 55%,#120d18 100%)',
    white: 'radial-gradient(80% 60% at 50% 30%, rgba(255,255,255,.75), transparent 66%), linear-gradient(180deg,#dfe6f2 0%,#aeb8cf 100%)'
  };

  const $ = (id) => document.getElementById(id);
  const world = $('world');
  // 画布已换成 LiteGraph，旧的 SVG 连线层不再存在 —— 给个空对象兜住遗留调用
  const wires = $('wires') || {
    addEventListener: function () {}, setAttribute: function () {},
    appendChild: function () {}, removeChild: function () {},
    querySelectorAll: function () { return []; }, innerHTML: ''
  };
  const wrap = $('canvasWrap');

  let zoom = 1, panX = 0, panY = 0;
  let selId = null;
  let selLink = null;     // 选中的连线索引
  let graphProject = null; // 后端返回的 project 元信息（保存时原样回传）
  let graphCharacters = null; // 后端返回的 characters 角色表（PUT 必填）
  let graphChapters = null; // 后端章节索引；无此字段时保持 null 并隐藏章节树
  let graphFlags = null;    // 后端 graph.flags（旗标声明），试玩按它初始化，保存时原样回传
  let activeChapterId = null;
  const collapsedActs = new Set();
  // 后端原始节点，按 id 存。★ 不放节点对象上：nodes 会被 syncFromEditor/dump 反复重建，
  //   _raw 挂在对象上迟早丢（实测保存一次 8 个节点只剩 1 个还带着），
  //   一丢就会把资产路径、provider、生成记录统统冲成前端默认值。
  let rawById = {};
  let pendingFrom = null; // 连线模式：{from, port}

  /* ---------------- 撤销 / 重做 ---------------- */
  const HISTORY_LIMIT = 60;
  const hist = { undo: [], redo: [] };

  function snap() {
    return JSON.stringify({ nodes: nodes, links: links });
  }

  /** 把「操作前的状态」压栈。data 不传就用当前状态 */
  function pushHistory(label, data) {
    hist.undo.push({ label: label, data: data || snap() });
    if (hist.undo.length > HISTORY_LIMIT) hist.undo.shift();
    hist.redo.length = 0;
    updateHistoryUI();
    scheduleAutosave();          // 任何改数据的操作都会走到这里 → 自动保存
  }

  /* ---------------- 自动保存 ----------------
     改一下东西就排队存一次（1.5 秒内连着改只存最后一次），
     工具栏会显示「已自动保存 08:35:12」，不用再记得点「保存」。
     入口挂在 pushHistory 上：加节点 / 删节点 / 连线 / 断线 / 改台词 / 自动排布都经过它。 */
  let autosaveTimer = null;
  let autosaveBusy = false;
  let autosaveDirty = false;

  function setSaveBadge(text, cls) {
    const el = $('saveState');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'save-state' + (cls ? ' ' + cls : '');
  }

  function scheduleAutosave() {
    if (GalAPI.mode !== 'real' || !projectId) return;   // 没真后端 / 没项目，没什么可存
    autosaveDirty = true;
    setSaveBadge('待保存…', 'pending');
    if (autosaveTimer) clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(runAutosave, 1500);
  }

  async function runAutosave() {
    autosaveTimer = null;
    if (autosaveBusy) {                       // 上一次还没写完，稍后再排
      autosaveTimer = setTimeout(runAutosave, 600);
      return;
    }
    if (!projectId || GalAPI.mode !== 'real' || !autosaveDirty) return;
    autosaveBusy = true;
    autosaveDirty = false;
    setSaveBadge('保存中…', 'saving');
    let ok = false;
    try {
      ok = await saveGraphToBackend(true);     // quiet：不弹 toast，状态只看徽标
    } catch (e) {
      ok = false;
    }
    autosaveBusy = false;
    if (ok) {
      const d = new Date();
      const p = (n) => String(n).padStart(2, '0');
      setSaveBadge('已自动保存 ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds()), 'saved');
      // 同步轮询基线，否则 2 秒后的比对会把刚存的结果当成"又变了"，白存一次
      try { lastSnapForWatch = snap(); } catch (e) {}
    } else {
      setSaveBadge('自动保存失败（点「保存」重试）', 'error');
      autosaveDirty = true;                   // 留着，下次改动再试
    }
  }

  /* 拖动节点是 LiteGraph 内部改的位置，不走 pushHistory（所以不会触发上面的排队）。
     这里每 2 秒比一次整图指纹兜底：位置一动，2 秒内就会自动存下来。 */
  let lastSnapForWatch = null;
  setInterval(() => {
    if (GalAPI.mode !== 'real' || !projectId) return;
    const ed = $('view-editor');
    if (!ed || !ed.classList.contains('active')) return;   // 只在编排页盯着
    let now = '';
    try { now = snap(); } catch (e) { return; }
    if (lastSnapForWatch === null) { lastSnapForWatch = now; return; }
    if (now !== lastSnapForWatch) {
      lastSnapForWatch = now;
      scheduleAutosave();
    }
  }, 2000);

  /* ---------------- 气泡确认（替代 window.confirm） ----------------
     ✗ window.confirm 在桌面壳里会弹系统对话框：标题顶着 127.0.0.1:8799、样式跟界面完全不搭，
       而且**会阻塞脚本**（自动化测试直接卡死，实测踩过）。
     ✓ 改成贴着按钮的小气泡，点外面自动关。 */
  let pcEl = null;

  function closePopconfirm() {
    if (pcEl && pcEl.parentNode) pcEl.parentNode.removeChild(pcEl);
    pcEl = null;
  }

  function popconfirm(anchor, message, onOk, okText) {
    closePopconfirm();
    if (!anchor) return;

    const box = document.createElement('div');
    box.className = 'popconfirm';
    const msg = document.createElement('div');
    msg.className = 'pc-msg';
    msg.textContent = message;
    const acts = document.createElement('div');
    acts.className = 'pc-acts';
    const yes = document.createElement('button');
    yes.className = 'btn sm';
    yes.dataset.yes = '1';
    yes.textContent = okText || '确定';
    const no = document.createElement('button');
    no.className = 'btn sm ghost';
    no.dataset.no = '1';
    no.textContent = '取消';
    acts.appendChild(yes);
    acts.appendChild(no);
    box.appendChild(msg);
    box.appendChild(acts);
    document.body.appendChild(box);

    // 贴按钮下方，别超出窗口右边
    const r = anchor.getBoundingClientRect();
    const bw = 262;
    box.style.left = Math.max(8, Math.min(r.left, window.innerWidth - bw - 10)) + 'px';
    box.style.top = Math.round(r.bottom + 8) + 'px';

    yes.addEventListener('click', (ev) => {
      ev.stopPropagation();
      closePopconfirm();
      if (onOk) onOk();
    });
    no.addEventListener('click', (ev) => {
      ev.stopPropagation();
      closePopconfirm();
    });
    pcEl = box;

    // 点别处关掉（延迟一拍，否则这一下点击立刻把它关掉）
    setTimeout(() => {
      const h = (ev) => {
        if (pcEl && !pcEl.contains(ev.target)) closePopconfirm();
        document.removeEventListener('pointerdown', h, true);
      };
      document.addEventListener('pointerdown', h, true);
    }, 0);
  }

  /* ---------------- 应用内输入框（替代 window.prompt） ----------------
     ✗ window.prompt 在桌面壳里弹出的是 WebView2 系统对话框：标题顶着 127.0.0.1:8799、
        白底黑框跟界面完全不搭，而且**同步阻塞脚本**（自动化直接卡死）。
     ✓ 换成自己的遮罩浮层，返回 Promise<值>，取消给 null。 */
  let askEl = null;

  function closeAskInput() {
    if (askEl && askEl.parentNode) askEl.parentNode.removeChild(askEl);
    askEl = null;
  }

  function askInput(opts) {
    opts = opts || {};
    closeAskInput();

    return new Promise((resolve) => {
      const mask = document.createElement('div');
      mask.className = 'gal-modal-mask';

      const box = document.createElement('div');
      box.className = 'gal-modal';

      const title = document.createElement('div');
      title.className = 'gm-title';
      title.textContent = opts.title || '输入';

      const field = document.createElement(opts.multiline ? 'textarea' : 'input');
      field.className = 'gm-field';
      if (!opts.multiline) field.type = 'text';
      field.value = opts.value == null ? '' : String(opts.value);
      if (opts.placeholder) field.placeholder = opts.placeholder;
      if (opts.multiline) field.rows = Math.min(8, Math.max(3, String(field.value).split('\n').length + 1));

      const acts = document.createElement('div');
      acts.className = 'gm-acts';
      const ok = document.createElement('button');
      ok.className = 'btn sm';
      ok.dataset.ok = '1';
      ok.textContent = opts.okText || '确定';
      const no = document.createElement('button');
      no.className = 'btn sm ghost';
      no.dataset.no = '1';
      no.textContent = '取消';
      acts.appendChild(ok);
      acts.appendChild(no);

      box.appendChild(title);
      box.appendChild(field);
      if (opts.hint) {
        const hint = document.createElement('div');
        hint.className = 'gm-hint';
        hint.textContent = opts.hint;
        box.appendChild(hint);
      }
      box.appendChild(acts);
      mask.appendChild(box);
      document.body.appendChild(mask);
      askEl = mask;

      let done = false;
      const finish = (val) => {
        if (done) return;
        done = true;
        document.removeEventListener('keydown', onKey, true);
        closeAskInput();
        resolve(val);
      };

      const onKey = (ev) => {
        if (ev.key === 'Escape') {
          ev.preventDefault();
          ev.stopPropagation();
          finish(null);
        } else if (ev.key === 'Enter' && (!opts.multiline || ev.ctrlKey || ev.metaKey)) {
          ev.preventDefault();
          ev.stopPropagation();
          finish(field.value);
        }
      };

      ok.addEventListener('click', () => finish(field.value));
      no.addEventListener('click', () => finish(null));
      // 点遮罩空白处 = 取消（点面板内部不关）
      mask.addEventListener('pointerdown', (ev) => {
        if (ev.target === mask) finish(null);
      });
      document.addEventListener('keydown', onKey, true);

      setTimeout(() => {
        try { field.focus(); field.select(); } catch (e) {}
      }, 0);
    });
  }

  /* ---------------- 原生弹窗防盗门 ----------------
     桌面壳里 window.prompt / alert / confirm 都会弹 WebView2 系统对话框（标题是 127.0.0.1:8799）。
     这里全部接管，保证界面上永远不再出现那种框。
     ⚠ 必须说明的取舍：原生 API 是**同步**的，接管后只能同步返回 null / false。
       新代码要输入或确认，请直接 await askInput(...) / 调 popconfirm(...)，
       不要指望 window.prompt 能拿到用户输入。 */
  function installNativeDialogGuard() {
    if (window.__galDialogGuard) return;
    window.__galDialogGuard = true;

    window.alert = function (msg) {
      try { toast(String(msg == null ? '' : msg)); } catch (e) {}
    };

    window.prompt = function (title, def) {
      console.warn('[gal] window.prompt 已被接管为应用内浮层；同步返回值恒为 null，请改用 askInput()');
      try {
        askInput({ title: title || '输入', value: def == null ? '' : String(def) });
      } catch (e) {}
      return null;
    };

    window.confirm = function (msg) {
      console.warn('[gal] window.confirm 已被接管；同步返回值恒为 false，请改用 popconfirm()');
      try { toast(String(msg == null ? '' : msg)); } catch (e) {}
      return false;
    };
  }

  installNativeDialogGuard();

  function applySnapshot(json) {
    const d = JSON.parse(json);
    nodes = d.nodes;
    links = d.links;
    selId = null;
    pendingFrom = null;
    $('inspector').classList.remove('show');
    render();
  }

  function undo() {
    if (!hist.undo.length) { toast('没有可撤销的操作'); return; }
    const it = hist.undo.pop();
    hist.redo.push({ label: it.label, data: snap() });
    applySnapshot(it.data);
    addLog('↶ 撤销：' + it.label);
    updateHistoryUI();
  }

  function redo() {
    if (!hist.redo.length) { toast('没有可重做的操作'); return; }
    const it = hist.redo.pop();
    hist.undo.push({ label: it.label, data: snap() });
    applySnapshot(it.data);
    addLog('↷ 重做：' + it.label);
    updateHistoryUI();
  }

  function updateHistoryUI() {
    const u = $('btnUndo'), r = $('btnRedo');
    if (u) u.disabled = hist.undo.length === 0;
    if (r) r.disabled = hist.redo.length === 0;
  }

  /* ---------------- 项目页 ---------------- */
  /** 相对时间：刚刚 / 5 分钟前 / 3 小时前 / 2 天前 / 2026-11-06 */
  function relTime(iso) {
    if (!iso) return '—';
    const t = Date.parse(iso);
    if (!t) return '—';
    const d = Date.now() - t;
    if (d < 60e3) return '刚刚';
    if (d < 3600e3) return Math.floor(d / 60e3) + ' 分钟前';
    if (d < 86400e3) return Math.floor(d / 3600e3) + ' 小时前';
    if (d < 30 * 86400e3) return Math.floor(d / 86400e3) + ' 天前';
    return new Date(t).toLocaleDateString('zh-CN');
  }

  let projList = [];

  async function renderProjects() {
    const grid = $('projGrid');
    const hint = $('projHint');
    const cnt = $('projCount');
    if (!grid) return;

    if (GalAPI.mode !== 'real') {
      if (hint) hint.textContent = '模拟模式';
      if (cnt) cnt.textContent = '0';
      grid.innerHTML = '<div class="proj-card empty">模拟模式下没有真实项目。\n切到「真后端」再看。</div>';
      return;
    }

    if (hint) hint.textContent = '正在读取…';
    try {
      projList = await GalAPI.listProjects();
    } catch (e) {
      projList = [];
      if (hint) hint.textContent = '读取失败';
      grid.innerHTML = '<div class="proj-card empty">读取项目列表失败：'
        + esc(e.message || String(e)) + '</div>';
      return;
    }

    if (cnt) cnt.textContent = String(projList.length);
    if (hint) hint.textContent = projList.length ? '按最近改动排序' : '还没有项目';

    if (!projList.length) {
      grid.innerHTML = '<div class="proj-card empty">还没有项目。上面输入一个名字，点「新建项目」。</div>';
      return;
    }

    grid.innerHTML = projList.map((p) => {
      const cur = p.id === projectId;
      return '<div class="proj-card' + (cur ? ' cur' : '') + '" data-id="' + esc(p.id) + '">'
        + '<div class="pc-name">' + esc(p.name || '(未命名)')
        + (cur ? '<span class="pc-cur">当前</span>' : '') + '</div>'
        + '<div class="pc-meta">'
        + '<span><b>' + (p.node_count || 0) + '</b> 节点</span>'
        + '<span><b>' + (p.ending_count || 0) + '</b> 结局</span>'
        + '<span>' + (p.has_export ? '<b>已导出</b>' : '未导出') + '</span>'
        + '<span>' + esc(relTime(p.updated_at)) + '</span>'
        + '</div>'
        + '<div class="pc-id">' + esc(p.id) + '</div>'
        + '<div class="pc-act">'
        + '<button class="btn sm" data-act="open">打开</button>'
        + '<button class="btn sm ghost proj-del" data-act="del">删除</button>'
        + '</div></div>';
    }).join('');

    grid.querySelectorAll('.proj-card[data-id]').forEach((card) => {
      // 整张卡片都能点进项目：这是「选择项目 → 进入项目」的主路径。
      // 卡片里的按钮会 stopPropagation，不会重复触发。
      card.addEventListener('click', async () => {
        await openProjectById(card.dataset.id);
      });
      card.querySelectorAll('button[data-act]').forEach((btn) => {
        btn.addEventListener('click', async (ev) => {
          ev.stopPropagation();
          const id = card.dataset.id;
          if (btn.dataset.act === 'open') await openProjectById(id);
          else await removeProjectById(id, btn);
        });
      });
    });
  }

  /** 打开指定项目：拉图 → 套进画布 → 记住 → 跳到编排 */
  async function openProjectById(id) {
    const meta = projList.find((x) => x.id === id) || {};
    try {
      const g = await GalAPI.getGraph(id);
      // ★ 只有"图读不出来"才拦。0 节点的空项目必须照样能进 ——
      //   以前这里要求 nodes 非空，结果新建的空项目点「打开」直接弹
      //   "这个项目还没有节点"，用户永远进不了编辑器（实测踩到）。
      if (!g || !Array.isArray(g.nodes)) {
        toast('这个项目的剧情图读不出来（可能已损坏）');
        return;
      }
      projectId = id;
      const c = applyGraph(g);
      // 名字兜底：项目列表里查不到时，退回刚才记住的那份，别把名字丢了
      let displayName = meta.name;
      if (!displayName) {
        try { displayName = localStorage.getItem('gal_project_name') || ''; } catch (e) {}
      }
      rememberProject(id, displayName);
      const nmEl = document.querySelector('.proj-name input');
      if (nmEl && meta.name) nmEl.value = meta.name;
      if (!c.nodes) {
        toast('空项目：点画布上方的「+ 节点」手动加，或回接口配置写需求后生成');
      } else {
        toast('已打开「' + (meta.name || id.slice(0, 8)) + '」· ' + c.nodes + ' 节点');
      }
      setSaveBadge('已自动保存', 'saved');   // 刚打开 = 和后端一致
      go('editor');
      // 续写状态：打开项目就能看到「上次写到哪、盘上的图对不对得上检查点」。
      // 拿不到（mock 模式 / 老项目没检查点）就什么都不说，别拿提示打扰人。
      GalAPI.getResume(id).then((r) => {
        if (!r) return;
        const cp = r.checkpoint || {};
        if (!cp.status || cp.status === 'empty') return;
        const done = (r.done || []).length;
        const writing = (r.writing || [])[0];
        const where = writing ? '，' + writing + ' 写到一半'
          : (done ? '，已完成 ' + done + ' 章' : '');
        const warn = cp.status === 'regressed'
          ? ' —— 内容比检查点少，接着写之前编剧会先看证据'
          : (cp.status === 'corrupt' ? ' —— 图读不出来，点生成会自动回滚到最近一份快照' : '');
        addLog('续写检查点 ' + cp.status + '（' + (cp.nodes || 0) + ' 节点' + where + '）' + warn);
      }).catch(() => {});
      renderProjects();
    } catch (e) {
      toast('打开失败：' + (e.message || e));
    }
  }

  /** 删项目：点两下才真删（第一次变「确认删除？」，3 秒不点自动复原）。
   *  不用 window.confirm —— 桌面壳里它会阻塞脚本，而且样式跟界面不搭。 */
  async function removeProjectById(id, btnEl) {
    const meta = projList.find((x) => x.id === id) || {};
    const nm = meta.name || id.slice(0, 8);

    if (!btnEl || btnEl.dataset.armed !== '1') {
      if (btnEl) {
        btnEl.dataset.armed = '1';
        btnEl.textContent = '确认删除？';
        btnEl.classList.add('armed');
        setTimeout(() => {
          if (btnEl.dataset.armed === '1') {
            btnEl.dataset.armed = '';
            btnEl.textContent = '删除';
            btnEl.classList.remove('armed');
          }
        }, 3000);
      }
      return;
    }

    btnEl.dataset.armed = '';
    btnEl.textContent = '删除中…';
    btnEl.classList.remove('armed');
    try {
      await GalAPI.deleteProject(id);
      if (projectId === id) { projectId = null; forgetProject(); }
      toast('已删除「' + nm + '」');
      await renderProjects();
    } catch (e) {
      toast('删除失败：' + (e.message || e));
      btnEl.textContent = '删除';
    }
  }

  async function createProjectFromInput() {
    const inp = $('newProjName');
    const name = (inp && inp.value || '').trim();
    if (!name) { toast('先给项目起个名字'); if (inp) inp.focus(); return; }
    if (GalAPI.mode !== 'real') { toast('模拟模式下不能新建项目'); return; }
    const btn = $('btnNewProject');
    if (btn) btn.disabled = true;
    try {
      const p = await GalAPI.ensureProject(name);
      // ★ 建完立刻把 id 和名字记住：不要等 openProjectById 去项目列表里再查一遍名字。
      //   以前那一步要是没走通（列表没刷新 / applyGraph 抛错），名字就丢了，
      //   后面 ensureProjectId() 还会拿时间戳再建一个「未命名 xxx」的项目（实测踩到）。
      if (p && p.id) rememberProject(p.id, name);
      if (inp) inp.value = '';
      toast('已新建「' + name + '」');
      await renderProjects();
      // 新建后直接打开它，省一步
      if (p && p.id) await openProjectById(p.id);
    } catch (e) {
      toast('新建失败：' + (e.message || e));
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* ---------------- 视图切换 ---------------- */
  const views = {
    projects: $('view-projects'),
    setup: $('view-setup'),
    editor: $('view-editor'),
    assets: $('view-assets'),
    export: $('view-export'),
    settings: $('view-settings')
  };

  function go(view) {
    if (view === 'play') { openPlayer(); return; }
    Object.keys(views).forEach((k) => views[k].classList.toggle('active', k === view));
    document.querySelectorAll('#nav button').forEach((b) => {
      b.classList.toggle('active', b.dataset.view === view);
    });
    // 项目工作区导航「顶替」顶层导航：进项目后 #nav 收起来、#projBar 顶上去，
    // 两条都长在同一个标题栏里，不会多出一排把内容往下挤。
    const bar = document.getElementById('projBar');
    const topNav = document.getElementById('nav');
    if (bar) {
      const inProject = view === 'editor' || view === 'assets' || view === 'export';
      bar.hidden = !inProject;
      if (topNav) topNav.hidden = inProject;
      if (inProject) {
        document.querySelectorAll('#projBar [data-pgo]').forEach((b) => {
          b.classList.toggle('active', b.dataset.pgo === view);
        });
        const nameEl = document.getElementById('pbName');
        const titleEl = document.getElementById('projTitle');
        if (nameEl) nameEl.textContent = (titleEl && titleEl.textContent) || '未命名项目';
      }
    }
    if (view === 'projects') {
      renderProjects();          // 每次进来都重读，磁盘上项目可能已经变了
    }
    if (view === 'editor') {
      render();
      requestAnimationFrame(() => requestAnimationFrame(fitView));
      setTimeout(fitView, 120);   // 兜底：字体/布局稳定后再校正一次
    }
    if (view === 'export') {
      renderRenpy();
      syncPackPanel();       // 进打包页时把当前的游戏名/图标填进去
    }
    if (view === 'assets') {
      // 素材库属于「当前项目」：每次进来按当前绑定的项目重载，别拿页面上的旧数据糊弄
      if (window.__galAssetsEnter) window.__galAssetsEnter();
    }
    if (view === 'settings') {
      loadSettings();
    }
  }
  document.querySelectorAll('#nav button').forEach((b) => {
    b.addEventListener('click', () => go(b.dataset.view));
  });
  // 项目工作区导航条（进入项目后的 编排 / 试玩 / 打包）
  document.querySelectorAll('#projBar [data-pgo]').forEach((b) => {
    b.addEventListener('click', () => {
      if (b.dataset.pgo === 'play') { openPlayer(); return; }
      go(b.dataset.pgo);
    });
  });
  if ($('pbBack')) $('pbBack').addEventListener('click', () => go('projects'));
  // 素材库入口放在「剧情编排」里（它属于项目），顶部导航不再有这一项
  const btnOpenAssets = $('btnOpenAssets');
  if (btnOpenAssets) btnOpenAssets.addEventListener('click', () => go('assets'));
  const btnBackToEditor = $('btnBackToEditor');
  if (btnBackToEditor) btnBackToEditor.addEventListener('click', () => go('editor'));
  $('btnToEditor').addEventListener('click', async () => {
    await saveProviders(false);
    go('editor');
  });

  // 项目页按钮
  if ($('btnNewProject')) $('btnNewProject').addEventListener('click', createProjectFromInput);
  if ($('btnRefreshProjects')) $('btnRefreshProjects').addEventListener('click', renderProjects);
  if ($('newProjName')) {
    $('newProjName').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') createProjectFromInput();   // 回车直接建，不用去点按钮
    });
  }

  /* ---------------- 渲染节点 ---------------- */
  function waveHTML(seed) {
    const hs = [5, 9, 6, 12, 8, 11, 7, 4, 10, 6, 9, 5];
    let s = '';
    for (let i = 0; i < hs.length; i++) {
      const h = Math.max(3, (hs[(i + seed) % hs.length]));
      s += '<i style="height:' + h + 'px"></i>';
    }
    return s;
  }

  function nodeHTML(n) {
    const m = TYPE_META[n.type] || TYPE_META.scene;
    const outCount = n.type === 'choice' ? n.choices.length : (n.type === 'ending' ? 0 : 1);
    const inCount = n.type === 'start' ? 0 : 1;

    let ports = '';
    if (inCount) ports += '<div class="port in" data-port="in" title="输入"></div>';
    for (let i = 0; i < outCount; i++) {
      const label = n.type === 'choice' ? (i === 0 ? 'A' : i === 1 ? 'B' : i === 2 ? 'C' : 'D') : '';
      const topStyle = n.type === 'choice' ? ' style="top:' + (56 + i * 30) + 'px"' : '';
      ports += '<div class="port out" data-port="' + i + '"' + topStyle + ' title="输出 ' + (i + 1) + '">' +
        (label ? '<span class="pl">' + label + '</span>' : '') + '</div>';
    }

    const isEnding = n.type === 'ending';
    const badge = isEnding
      ? '<div class="badge ' + (n.endKind === 'true' ? '' : n.endKind === 'alt' ? 'alt' : '') + '" style="' +
        (n.endKind === 'bad' ? 'background:linear-gradient(120deg,#64748b,#475569);box-shadow:none;' :
         n.endKind === 'alt' ? 'background:linear-gradient(120deg,#6366f1,#22d3ee);box-shadow:none;' : '') + '">' +
        (n.endKind === 'true' ? 'TRUE END' : n.endKind === 'bad' ? 'BAD END' : 'HIDDEN') + '</div>'
      : '';

    const voiceBlock = isEnding ? '' :
      '<div class="voice' + (n.voice > 0 ? '' : ' pending') + '">' +
        '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 9v6M8 5v14M12 8v8M16 6v12M20 10v4"/></svg>' +
        '<span>' + (n.voice > 0 ? n.voice.toFixed(1) + 's' : '待生成') + '</span>' +
        '<span class="wf">' + waveHTML(n.idx ? parseInt(n.idx, 10) : 0) + '</span>' +
      '</div>';

    const useRealBg = !!(n.bgAsset && GalAPI.mode === 'real');
    const useRealChar = !!(n.charAsset && GalAPI.mode === 'real');
    const bgStyle = useRealBg
      ? 'background-image:url(\'' + GalAPI.assetURL(projectId, n.bgAsset) + '\');background-size:cover;background-position:center'
      : 'background:' + (BG_GRAD[n.bg] || BG_GRAD.rainy);
    const charInner = useRealChar
      ? '<div style="position:absolute;inset:0;background-image:url(\'' + GalAPI.assetURL(projectId, n.charAsset) + '\');background-size:contain;background-position:bottom center;background-repeat:no-repeat"></div>'
      : (isEnding
        ? '<div class="thumb-missing">无立绘</div>'
        : '<div style="position:absolute;left:50%;top:14%;width:30%;height:34%;transform:translateX(-50%);border-radius:50% 50% 44% 44%;background:radial-gradient(60% 60% at 38% 30%,#c6cddb,#e7eaf1 70%)"></div>' +
          '<div style="position:absolute;left:50%;bottom:0;width:76%;height:52%;transform:translateX(-50%);border-radius:46% 46% 0 0/34% 34% 0 0;background:linear-gradient(180deg,#dde2eb,#edf0f5)"></div>');

    return '' +
      '<div class="nh" style="background:' + m.bg + '">' +
        '<span class="kk" style="background:' + m.color + '"></span>' +
        '<span>' + (isEnding ? n.title : n.idx + ' · ' + n.title) + '</span>' +
        '<span class="idx">' + m.label + '</span>' +
      '</div>' +
      badge +
      '<div class="nb">' +
        '<div class="thumbs">' +
          '<div class="thumb-bg" style="' + bgStyle + '">' +
            '<span class="lb">bg · ' + (n.bg === 'rainy' ? '雨夜' : n.bg === 'dusk' ? '黄昏' : '清晨') + '</span>' +
          '</div>' +
          '<div class="thumb-ch" style="background:#f7f8fa">' +
            charInner +
            '<span class="lb">char</span>' +
          '</div>' +
        '</div>' +
        '<div class="line">' + (n.who ? '<span class="who">' + esc(n.who) + '　</span>' : '') + esc(n.text) + '</div>' +
        voiceBlock +
      '</div>' +
      ports;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  /* ---------------- 画布：LiteGraph 内核 ---------------- */
  let editorReady = false;

  function initEditor() {
    if (editorReady) return;
    GalEditor.init($('galCanvas'));
    // 画布层拿不到 projectId：把「素材相对路径 → 可访问 URL」的换算交给它，
    // 卡片上的背景 / 立绘预览统一走这里（换项目、换后端地址都不用改画布代码）。
    GalEditor.api.assetURL = (rel) => {
      try { return rel ? GalAPI.assetURL(projectId, rel) : ''; } catch (_) { return ''; }
    };
    GalEditor.api.onSelect = (galId) => {
      selId = galId || null;
      if (galId) {
        const n = nodes.filter((x) => x.id === galId)[0];
        if (n && typeof fillInspector === 'function') fillInspector(n);
      }
    };
    GalEditor.api.onChange = () => { syncFromEditor(); };
    // 拖动节点结束：把坐标同步回数据并触发自动保存（不然拖完保存位置会丢）
    GalEditor.api.onMove = () => { syncFromEditor(); scheduleAutosave(); };
    GalEditor.api.onDouble = (node) => {
      const id = node.properties.galId;
      const n = nodes.filter((x) => x.id === id)[0];
      if (!n) return;
      // 双击改台词：走应用内浮层，不再弹 WebView2 原生框
      askInput({
        title: '台词',
        value: n.text || '',
        multiline: true,
        okText: '确定',
        placeholder: '这一句显示在屏幕上的文字',
        hint: '直接回车换行，Ctrl+Enter 确定',
      }).then((v) => {
        if (v === null || v === n.text) return;
        if (nodes.indexOf(n) < 0) return;      // 编辑期间这个节点被删了，丢弃
        pushHistory('编辑台词');
        // ★ 写进原始数据的第 1 句，而不是只改画布上的合并串：
        //   多句节点里合并串会被保存逻辑当成第 1 句，等于把后面的台词吃进第一句。
        let raw = rawById[n.id];
        if (!raw) { raw = nodeSkeleton(n); rawById[n.id] = raw; }
        if (!Array.isArray(raw.lines) || !raw.lines.length) {
          raw.lines = [{ who: n.who || '', text: v, voice: null, dur: 1 }];
        } else {
          raw.lines[0].text = v;
        }
        n.text = raw.lines.map((l) => l.text).join(' ');
        n.who = raw.lines[0].who || '';
        GalEditor.updateNode(n.id, { text: n.text, who: n.who });
        if (selId === n.id) renderInspector(n);
        scheduleAutosave();
      });
    };
    editorReady = true;
  }

  function chapterNodeIds(chapter) {
    const ids = chapter && (chapter.node_ids || chapter.nodes);
    if (!Array.isArray(ids)) return [];
    return ids.map((n) => typeof n === 'string' ? n : n && n.id).filter(Boolean);
  }

  function visibleChapter() {
    return activeChapterId && Array.isArray(graphChapters)
      ? graphChapters.filter((c) => c && c.id === activeChapterId)[0] : null;
  }

  function renderChapterTree() {
    const panel = $('chapterPanel');
    if (!panel) return;
    panel.hidden = !Array.isArray(graphChapters) || !graphChapters.length;
    if (panel.hidden) { activeChapterId = null; return; }
    const groups = [];
    graphChapters.forEach((c, i) => {
      if (!c || !c.id) return;
      const act = c.act_title || c.act || '未分幕';
      let g = groups.filter((x) => x.title === act)[0];
      if (!g) { g = { title: act, chapters: [] }; groups.push(g); }
      g.chapters.push(c);
    });
    $('chapterTree').innerHTML = groups.map((g) => {
      const closed = collapsedActs.has(g.title);
      return '<div class="act"><button class="act-row' + (closed ? ' collapsed' : '') +
        '" data-act="' + esc(g.title) + '" aria-expanded="' + (!closed) + '"><span class="chev">▼</span>' + esc(g.title) +
        '</button><div class="act-chapters' + (closed ? ' collapsed' : '') + '">' + g.chapters.map((c) => {
          const state = c.status === 'done' ? 'done' : c.status === 'writing' ? 'writing' : 'pending';
          const icon = state === 'done' ? '✓' : state === 'writing' ? '▶' : '○';
          const label = state === 'done' ? '已完成' : state === 'writing' ? '写作中…' : '未开始';
          return '<button class="chapter-row ' + state + (c.id === activeChapterId ? ' active' : '') +
            '" data-chapter="' + esc(c.id) + '" title="' + esc(c.title || c.id) + ' · ' + label + '">' +
            '<span class="chapter-state">' + icon + '</span><span class="chapter-name">' + esc(c.title || c.id) +
            '</span><span class="chapter-meta">' + chapterNodeIds(c).length + ' 节点</span></button>';
        }).join('') + '</div></div>';
    }).join('');
    $('btnShowAll').classList.toggle('on', !activeChapterId);
  }

  function selectChapter(id) {
    if (id && !graphChapters.some((c) => c.id === id)) return;
    if (editorReady) syncFromEditor();
    activeChapterId = id || null;
    renderChapterTree();
    render();
    requestAnimationFrame(() => GalEditor.fit());
  }

  $('chapterTree').addEventListener('click', (e) => {
    const act = e.target.closest('[data-act]');
    if (act) {
      const name = act.dataset.act;
      if (collapsedActs.has(name)) collapsedActs.delete(name); else collapsedActs.add(name);
      renderChapterTree();
      return;
    }
    const row = e.target.closest('[data-chapter]');
    if (row) selectChapter(row.dataset.chapter);
  });
  $('btnShowAll').addEventListener('click', () => selectChapter(null));

  /** 合并「本章节点的 choices」：章内出口用画布的最新值，章外出口原样保留。
   *
   *  为什么不能直接用 dump 的结果：画布只装了本章节点，指向章外的出口
   *  在 LiteGraph 里连不上目标、dump 不出来。直接覆盖等于把它们全删了。
   *  顺序按原 choices 走，保证出口编号（port）不重排 —— port 一变，
   *  edges 就对不上，后端会报「edges 与 choices 不一致」。
   */
  function mergeChapterChoices(orig, dumped, visible) {
    const fresh = (dumped && dumped.choices) ? dumped.choices.slice() : [];
    if (!orig || !orig.choices || !orig.choices.length) return fresh;
    const out = [];
    orig.choices.forEach((c) => {
      // ★ 空出口 / 占位槽不是「章外出口」：以前原样留着，再拼上 dump 的 4 个槽，
      //   每保存一次选项就涨一截（p_0492… 的 c002 涨到 26 个）。
      if (!c.to || c.placeholder) return;
      if (!visible.has(c.to)) { out.push(c); return; }             // 章外出口：原样留着
      const idx = fresh.findIndex((x) => x.to === c.to);
      if (idx >= 0) out.push(fresh.splice(idx, 1)[0]);            // 章内出口：用最新值
      // 找不到 = 用户把这条连线删了，不补回来
    });
    return out.concat(fresh);                                     // 新连的线追加到末尾
  }

  /** 把画布里的改动同步回全量图；单章视图中绝不能丢掉隐藏章节。 */
  function syncFromEditor() {
    const d = GalEditor.dump();
    const old = {};
    nodes.forEach((n) => { old[n.id] = n; });
    if (!visibleChapter()) {
      nodes = d.nodes.map((nn) => Object.assign({}, old[nn.id] || {}, nn));
      links = d.links;
    } else {
      // ★ 单章视图下画布只装了本章节点，dump() 看不到「指向章外」的那条出口：
      //   · 跨章边（上一章尾 → 本章头）：一端在章外 → 原来的 filter 会把它删掉
      //   · 本章节点指向章外的 choice：dump 推不出来 → choices 被截断
      //   2026-11-09 实测：在这一步点一次保存，把 509 条边的项目写成 442 条。
      //   所以两处都改成「以原图为准」把章外的东西补回来。
      const visible = new Set(d.nodes.map((n) => n.id));
      const merged = {};
      d.nodes.forEach((nn) => {
        const base = Object.assign({}, old[nn.id] || {}, nn);
        base.choices = mergeChapterChoices(old[nn.id], nn, visible);
        merged[nn.id] = base;
      });
      // 原地替换、保持节点顺序（以前「章外 + 本章」重新拼接，保存一次全书顺序就变）。
      nodes = nodes.filter((n) => !visible.has(n.id) || merged[n.id]).map((n) => merged[n.id] || n)
        .concat(d.nodes.filter((nn) => !old[nn.id]).map((nn) => merged[nn.id]));
      // 只保留「至少一端在章外」的旧边；章内边由 d.links 重建
      links = links.filter((l) => !(visible.has(l.from) && visible.has(l.to))).concat(d.links);
    }
    // 同一 (from, port) 只留一条（画布上的新值在后面，后者覆盖前者）——
    // 以前直接 concat，旧边和新边叠在一起：640 条边里 526 条是冗余。
    links = dedupeLinks(links);
    selLink = null;
  }

  function dedupeLinks(list) {
    const byKey = new Map();
    list.forEach((l) => { byKey.set(l.from + '\u0000' + (l.port || 0), l); });
    return Array.from(byKey.values());
  }

  /** 真正的出口：去掉画布空槽（placeholder）和没连目标的项。 */
  function realChoices(n) {
    return ((n && n.choices) || []).filter((c) => c && c.to && !c.placeholder);
  }

  let didFit = false;

  /** 节点有没有挤在一起（自动排布的触发条件） */
  function hasOverlap() {
    for (var i = 0; i < nodes.length; i++) {
      for (var j = i + 1; j < nodes.length; j++) {
        if (Math.abs(nodes[i].x - nodes[j].x) < 60 &&
            Math.abs(nodes[i].y - nodes[j].y) < 60) return true;
      }
    }
    return false;
  }

  /** 兜底排布：往右铺满一行再换行，**绝不铺成「4 列往下堆」** */
  /** 只重排坐标，不碰视图、不写历史 —— 首屏布局和按钮共用 */
  function layoutNodes() {
    // ★ 这里曾经是 4 列网格（`i % 4`）。后果：后端按章节算好的「主干往右、分支上下」
    //   一进前端就被覆盖成一张大网格 —— 510 个节点排成 128 行、纵向 3 万像素，
    //   主人原话「节点一直往画布右边延伸，不要往下排，像树枝叶一样」。
    //   现在改成「横向优先」：一行铺 12 个再换行，兜底也不会长成竖条。
    var perRow = 12, gapX = NODE_W + 44, gapY = NODE_H + 36;
    nodes.forEach(function (n, i) {
      n.x = 40 + (i % perRow) * gapX;
      n.y = 40 + Math.floor(i / perRow) * gapY;
    });
  }

  /** 坐标是否可信：至少 3 个不同列 → 说明是分层排布（不是全 0 / 全挤一列） */
  function hasSpreadColumns() {
    if (nodes.length < 3) return true;
    var seen = {}, cols = 0;
    for (var i = 0; i < nodes.length; i++) {
      var key = Math.round(nodes[i].x);
      if (!seen[key]) {
        seen[key] = 1;
        if (++cols >= 3) return true;
      }
    }
    return false;
  }

  function autoLayout(silent) {
    layoutNodes();
    if (!silent) pushHistory('自动排布');
    render();
    if (!silent) {
      addLog('已自动排布 ' + nodes.length + ' 个节点');
      toast('已排布，并按窗口适配');
    }
  }

  function render() {
    initEditor();
    // 编排页初始可能是 display:none，那时 canvas 尺寸是 0 —— 每次渲染都重新量
    GalEditor.fitCanvas();
    // ★ 这里原来还有一条「启动后 2.5 秒内一律重排」——删掉了。
    //   后端和内置演示数据的坐标本来就带着「主干往右、分支上下」的结构，
    //   那条规则一开就把它们全部抹平成一格一格的网格。
    //   现在只在坐标真的不可信（互相压住 / 全挤在一列）时才兜底排布。
    if (nodes.length && (hasOverlap() || !hasSpreadColumns())) {
      layoutNodes();
    }
    const chapter = visibleChapter();
    const visibleIds = chapter ? new Set(chapterNodeIds(chapter)) : null;
    const shownNodes = visibleIds ? nodes.filter((n) => visibleIds.has(n.id)) : nodes;
    const shownLinks = visibleIds ? links.filter((l) => visibleIds.has(l.from) && visibleIds.has(l.to)) : links;
    GalEditor.load(shownNodes, shownLinks);
    window.__galVisibleGraph = { chapter: activeChapterId, nodes: shownNodes.length, links: shownLinks.length, totalNodes: nodes.length };
    // 适配统一交给 autoFit：带防抖，等数据/窗口尺寸都稳定后再算一次；
    // 用户自己缩放过就不会再动他的视野。
    GalEditor.autoFit();
    updateZoomLabel();
  }

  /* ---------------- 连线绘制 ---------------- */
  function nodeEl(id) { return document.getElementById('nd-' + id); }

  function outPos(n, port) {
    const el = nodeEl(n.id);
    const h = el ? el.offsetHeight : 190;
    if (n.type === 'choice') {
      const top = 62 + port * 30;
      return { x: n.x + NODE_W, y: n.y + Math.min(top, h - 24) };
    }
    return { x: n.x + NODE_W, y: n.y + h / 2 };
  }

  function inPos(n) {
    const el = nodeEl(n.id);
    const h = el ? el.offsetHeight : 190;
    return { x: n.x, y: n.y + h / 2 };
  }

  function drawWires() {
    if (window.GalEditor && typeof GalEditor.redraw === 'function') { GalEditor.redraw(); }
    if (!wires) return;
    let html = '<defs>';
    ['#3b82f6', '#22c55e', '#f59e0b', '#f43f5e'].forEach((c, i) => {
      html += '<marker id="ar' + i + '" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">' +
        '<path d="M0 0 L10 5 L0 10 z" fill="' + c + '"/></marker>';
    });
    html += '</defs>';

    links.forEach((l, i) => {
      const a = nodes.find((x) => x.id === l.from);
      const b = nodes.find((x) => x.id === l.to);
      if (!a || !b) return;
      const p1 = outPos(a, l.port || 0);
      const p2 = inPos(b);
      const dx = Math.max(45, Math.abs(p2.x - p1.x) * 0.45);
      const d = 'M' + p1.x + ',' + p1.y + ' C' + (p1.x + dx) + ',' + p1.y + ' ' + (p2.x - dx) + ',' + p2.y + ' ' + p2.x + ',' + p2.y;
      const color = TYPE_META[a.type].color;
      const mi = Object.keys(TYPE_META).indexOf(a.type);
      const active = pendingFrom && pendingFrom.from === a.id && pendingFrom.port === (l.port || 0);
      const isSel = selLink === i;
      html += '<path class="wire' + (isSel ? ' sel' : active ? ' hl' : '') + '" d="' + d + '" stroke="' + color +
        '" marker-end="url(#ar' + mi + ')" opacity="' + (isSel || active ? 1 : 0.7) +
        '" style="color:' + color + '"></path>';
      // 透明加粗热区：让连线可以点中
      html += '<path class="wire-hit" d="' + d + '" data-i="' + i + '"></path>';
    });

    wires.innerHTML = html;
    // svg 尺寸覆盖所有节点
    let maxX = 0, maxY = 0;
    nodes.forEach((n) => { maxX = Math.max(maxX, n.x + NODE_W + 300); maxY = Math.max(maxY, n.y + 400); });
    wires.setAttribute('width', maxX);
    wires.setAttribute('height', maxY);
  }

  /* ---------------- 连线交互：点选中 / Delete 断开 / 拖拽连接 ---------------- */
  wires.addEventListener('pointerdown', (e) => {
    const hit = e.target.closest ? e.target.closest('.wire-hit') : null;
    if (!hit) return;
    e.stopPropagation();
    e.preventDefault();
    const i = parseInt(hit.getAttribute('data-i'), 10);
    selLink = (selLink === i) ? null : i;
    selId = null;
    document.querySelectorAll('.node').forEach((el) => el.classList.remove('sel'));
    $('inspector').classList.remove('show');
    drawWires();
    if (selLink !== null && links[selLink]) {
      const l = links[selLink];
      const a = nodes.find((x) => x.id === l.from);
      toast('已选中：' + (a ? a.title : l.from) + ' → ' + l.to + ' · 按 Delete 断开');
    }
  });

  function deleteLink() {
    if (selLink === null || !links[selLink]) { toast('先点一下要断开的那条线'); return; }
    const l = links[selLink];
    pushHistory('断开连线');
    links.splice(selLink, 1);
    selLink = null;
    drawWires();
    addLog('断开连线 ' + l.from + ' → ' + l.to);
    toast('已断开（Ctrl+Z 可撤回）');
  }

  let linking = null;

  function clientToWorld(cx, cy) {
    const r = wrap.getBoundingClientRect();
    return { x: (cx - r.left - panX) / zoom, y: (cy - r.top - panY) / zoom };
  }

  /** 从输出口开始拉线：拖动 → 松手在节点上连上；只点一下 → 再点目标节点连 */
  function startLinking(fromId, port, ev) {
    if (linking) return;
    const n = nodes.find((x) => x.id === fromId);
    if (!n) return;
    const p1 = outPos(n, port);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('class', 'wire-temp');
    wires.appendChild(path);

    const sx = ev.clientX, sy = ev.clientY;
    let moved = false;
    let snapNode = null;
    linking = { from: fromId, port: port };

    const clearSnap = () => {
      if (snapNode) {
        const el = nodeEl(snapNode.id);
        if (el) el.classList.remove('drop-target');
        snapNode = null;
      }
    };

    const onMove = (e) => {
      if (!linking) return;
      if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) > 4) moved = true;

      // —— 端口吸附：鼠标进到某个节点范围内就吸附它的输入口 ——
      const under = document.elementFromPoint(e.clientX, e.clientY);
      const tEl = under && under.closest ? under.closest('.node') : null;
      const tId = tEl ? tEl.dataset.id : null;
      if (tId && tId !== fromId) {
        if (!snapNode || snapNode.id !== tId) {
          clearSnap();
          snapNode = nodes.find((x) => x.id === tId) || null;
          if (snapNode) {
            const el = nodeEl(snapNode.id);
            if (el) el.classList.add('drop-target');
          }
        }
      } else {
        clearSnap();
      }

      const p2 = snapNode ? inPos(snapNode) : clientToWorld(e.clientX, e.clientY);
      const c = Math.max(45, Math.abs(p2.x - p1.x) * 0.45);
      path.setAttribute('d', 'M' + p1.x + ',' + p1.y + ' C' + (p1.x + c) + ',' + p1.y +
        ' ' + (p2.x - c) + ',' + p2.y + ' ' + p2.x + ',' + p2.y);
    };

    const onUp = (e) => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      try { path.remove(); } catch (err) {}
      const target = snapNode;
      clearSnap();
      linking = null;

      if (!moved) {
        pendingFrom = { from: fromId, port: port };
        drawWires();
        toast('再点一下目标节点就连上（也可以直接拖过去）');
        return;
      }

      let toId = target ? target.id : null;
      if (!toId) {
        const under = document.elementFromPoint(e.clientX, e.clientY);
        const t = under && under.closest ? under.closest('.node') : null;
        toId = t ? t.dataset.id : null;
      }
      if (!toId) { toast('松手在节点上才能连接'); drawWires(); return; }
      if (toId === fromId) { toast('不能连自己'); drawWires(); return; }
      pushHistory('连线');
      links = links.filter((l) => !(l.from === fromId && l.port === port));
      links.push({ from: fromId, port: port, to: toId });
      addLog('连线 ' + fromId + ' → ' + toId);
      toast('已连接到「' + ((nodes.find((x) => x.id === toId) || {}).title || toId) + '」');
      drawWires();
    };

    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    onMove(ev);
  }

  /* ---------------- Blender 式「一刀切断」（Alt + 拖动） ---------------- */
  const SVG_NS = 'http://www.w3.org/2000/svg';

  function bezierPoints(p1, p2, segs) {
    const c = Math.max(45, Math.abs(p2.x - p1.x) * 0.45);
    const c1 = { x: p1.x + c, y: p1.y };
    const c2 = { x: p2.x - c, y: p2.y };
    const pts = [];
    for (let i = 0; i <= segs; i++) {
      const t = i / segs, mt = 1 - t;
      pts.push({
        x: mt * mt * mt * p1.x + 3 * mt * mt * t * c1.x + 3 * mt * t * t * c2.x + t * t * t * p2.x,
        y: mt * mt * mt * p1.y + 3 * mt * mt * t * c1.y + 3 * mt * t * t * c2.y + t * t * t * p2.y
      });
    }
    return pts;
  }

  function segIntersect(a, b, c, d) {
    const rx = b.x - a.x, ry = b.y - a.y;
    const sx = d.x - c.x, sy = d.y - c.y;
    const den = rx * sy - ry * sx;
    if (Math.abs(den) < 1e-9) return false;
    const t = ((c.x - a.x) * sy - (c.y - a.y) * sx) / den;
    const u = ((c.x - a.x) * ry - (c.y - a.y) * rx) / den;
    return t >= 0 && t <= 1 && u >= 0 && u <= 1;
  }

  function startCut(ev) {
    const line = document.createElementNS(SVG_NS, 'path');
    line.setAttribute('class', 'cut-line');
    wires.appendChild(line);

    const a = clientToWorld(ev.clientX, ev.clientY);
    let b = a;

    const move = (e) => {
      b = clientToWorld(e.clientX, e.clientY);
      line.setAttribute('d', 'M' + a.x + ',' + a.y + ' L' + b.x + ',' + b.y);
    };

    const up = (e) => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      try { line.remove(); } catch (err) {}
      b = clientToWorld(e.clientX, e.clientY);

      const hit = [];
      links.forEach((l, i) => {
        const na = nodes.find((x) => x.id === l.from);
        const nb = nodes.find((x) => x.id === l.to);
        if (!na || !nb) return;
        const pts = bezierPoints(outPos(na, l.port || 0), inPos(nb), 24);
        for (let k = 0; k < pts.length - 1; k++) {
          if (segIntersect(pts[k], pts[k + 1], a, b)) { hit.push(i); break; }
        }
      });

      if (!hit.length) { toast('这一刀没切到线'); return; }
      pushHistory('切断连线');
      hit.sort((x, y) => y - x).forEach((i) => links.splice(i, 1));
      selLink = null;
      drawWires();
      addLog('切断了 ' + hit.length + ' 条连线');
      toast('切断了 ' + hit.length + ' 条线（Ctrl+Z 可撤回）');
    };

    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
    move(ev);
  }

  /* ---------------- 画布变换 ---------------- */
  function applyTransform() {
    if (!world) return;
    // LiteGraph 模式下视图变换全由 canvas 内核负责，旧 CSS 变换必须让路，
    // 否则两套 transform 叠加（画面偏移、鼠标坐标错位）。
    if (window.GalEditor) return;
    world.style.transform = 'translate(' + panX + 'px,' + panY + 'px) scale(' + zoom + ')';
    // 网格跟随
    const gx = panX % (24 * zoom);
    const gy = panY % (24 * zoom);
    wrap.style.setProperty('--gx', gx + 'px');
    wrap.style.setProperty('--gy', gy + 'px');
    wrap.style.backgroundSize = '100% 100%, ' + (24 * zoom) + 'px ' + (24 * zoom) + 'px, ' + (24 * zoom) + 'px ' + (24 * zoom) + 'px';
    updateZoomLabel();
  }

  function updateZoomLabel() {
    var z = (window.GalEditor && typeof GalEditor.getZoom === 'function') ? GalEditor.getZoom() : zoom;
    $('zoomInfo').textContent = Math.round(z * 100) + '%';
  }
  // 滚轮缩放由 LiteGraph 处理，这里定时把百分比同步到工具栏
  setInterval(updateZoomLabel, 400);

  function curZoom() {
    return (window.GalEditor && typeof GalEditor.getZoom === 'function') ? GalEditor.getZoom() : zoom;
  }

  function setZoom(z, cx, cy) {
    if (window.GalEditor) { GalEditor.setZoom(z); updateZoomLabel(); return; }
    const nz = Math.min(2.2, Math.max(0.28, z));
    if (cx == null) { cx = wrap.clientWidth / 2; cy = wrap.clientHeight / 2; }
    const k = nz / zoom;
    panX = cx - (cx - panX) * k;
    panY = cy - (cy - panY) * k;
    zoom = nz;
    applyTransform();
  }

  zoomIn.addEventListener('click', () => setZoom(curZoom() * 1.18));
  zoomOut.addEventListener('click', () => setZoom(curZoom() / 1.18));

  wrap.addEventListener('wheel', (e) => {
    // LiteGraph 模式：滚轮缩放完全交给 canvas 自己处理（它按鼠标位置算焦点）。
    // 这里必须让路，否则两套逻辑互相打架 → 焦点漂移。
    if (window.GalEditor) return;
    e.preventDefault();
    const r = wrap.getBoundingClientRect();
    setZoom(zoom * (e.deltaY > 0 ? 0.9 : 1.1), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });

  // 空白处拖动平移
  let panning = false, pSX = 0, pSY = 0, pOX = 0, pOY = 0;
  wrap.addEventListener('pointerdown', (e) => {
    // LiteGraph 模式：拖动画布 / 拖节点 / 拖连线全由 canvas 内核处理。
    // 这里若继续跑，setPointerCapture 会抢走 pointermove，节点就拖不起来了。
    if (window.GalEditor) return;
    if (e.target.closest('.node') || e.target.closest('.toolbar') || e.target.closest('.inspector') || e.target.closest('.legend') || e.target.closest('.add-menu')) return;

    // Alt + 拖动 = 一刀切断（Blender 手感）
    if (e.button === 0 && e.altKey) {
      e.preventDefault();
      e.stopPropagation();
      startCut(e);
      return;
    }

    const menu = $('addMenu');
    if (menu && menu.classList.contains('show')) menu.classList.remove('show');
    if (selLink !== null) { selLink = null; drawWires(); }
    panning = true; pSX = e.clientX; pSY = e.clientY; pOX = panX; pOY = panY;
    wrap.classList.add('panning');
    wrap.setPointerCapture(e.pointerId);
  });

  // 画布内右键不弹系统菜单（留给我们自己用）
  wrap.addEventListener('contextmenu', (e) => e.preventDefault());
  wrap.addEventListener('pointermove', (e) => {
    if (!panning) return;
    panX = pOX + (e.clientX - pSX);
    panY = pOY + (e.clientY - pSY);
    applyTransform();
  });
  wrap.addEventListener('pointerup', (e) => {
    if (!panning) return;
    panning = false;
    wrap.classList.remove('panning');
    try { wrap.releasePointerCapture(e.pointerId); } catch (err) {}
    if (pendingFrom) { pendingFrom = null; drawWires(); }
  });

  function fitView() {
    if (window.GalEditor) { GalEditor.fit(); return; }
    if (!nodes.length) return;
    let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
    nodes.forEach((n) => {
      const el = nodeEl(n.id);
      const w = el ? el.offsetWidth : NODE_W;
      const h = el ? el.offsetHeight : 190;
      minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x + w); maxY = Math.max(maxY, n.y + h);
    });

    const pad = 70;
    const w = wrap.clientWidth || 1200;
    const h = wrap.clientHeight || 700;
    const zx = (w - pad * 2) / Math.max(1, maxX - minX);
    const zy = (h - pad * 2) / Math.max(1, maxY - minY);
    zoom = Math.max(0.05, Math.min(zx, zy, 1.1));   // 下限放宽：上千节点是章内往右、章间换行，整图要能一次套进视口

    const center = () => {
      panX = (w - (maxX - minX) * zoom) / 2 - minX * zoom;
      panY = (h - (maxY - minY) * zoom) / 2 - minY * zoom;
    };
    center();

    // 兜底：算出来如果仍然越界，继续缩，保证整张图完整可见
    for (let i = 0; i < 5; i++) {
      const right = panX + maxX * zoom;
      const bottom = panY + maxY * zoom;
      const left = panX + minX * zoom;
      const top = panY + minY * zoom;
      if (right <= w - 6 && bottom <= h - 6 && left >= 6 && top >= 6) break;
      zoom *= 0.9;
      center();
    }
    applyTransform();
  }
  $('btnFit').addEventListener('click', fitView);

  // 自动排布：按拓扑层级
  function autoLayout() {
    const level = {};
    nodes.forEach((n) => { level[n.id] = 0; });
    for (let k = 0; k < 12; k++) {
      links.forEach((l) => {
        if (level[l.to] < level[l.from] + 1) level[l.to] = level[l.from] + 1;
      });
    }
    const groups = {};
    nodes.forEach((n) => { (groups[level[n.id]] = groups[level[n.id]] || []).push(n); });
    Object.keys(groups).forEach((lv) => {
      const arr = groups[lv];
      const totalH = arr.length * 210;
      let y = 320 - totalH / 2 + 60;
      arr.forEach((n) => {
        n.x = 40 + lv * 330;
        n.y = Math.round(y);
        y += 220;
      });
    });
    render();
    fitView();
    toast('已按层级自动排布');
  }
  $('btnLayout').addEventListener('click', autoLayout);

  /* 节点 id 必须唯一：同一毫秒内连续创建也不许撞车 */
  let nodeUid = 0;
  function newNodeId() {
    nodeUid += 1;
    return 'n' + Date.now().toString(36) + nodeUid.toString(36);
  }

  /* 新建节点：先选类型 */
  const NODE_DEFAULT_TEXT = {
    start: '故事从这里开始。',
    scene: '在这里写台词……',
    choice: '选择哪一个？',
    ending: '故事结束。'
  };

  function addNodeOfType(type) {
    if (!TYPE_META[type]) type = 'scene';
    pushHistory('添加节点');
    const maxX = nodes.length ? Math.max.apply(null, nodes.map((n) => n.x)) : 0;
    const lastY = nodes.length ? nodes[nodes.length - 1].y : 320;
    const n = {
      id: newNodeId(),
      type: type,
      idx: String(nodes.length + 1).padStart(2, '0'),
      title: type === 'start' ? '新序章' : type === 'choice' ? '新分支' : type === 'ending' ? '新结局' : '新场景',
      x: maxX + 330,
      y: Math.max(120, Math.min(620, lastY)),
      who: '',
      text: NODE_DEFAULT_TEXT[type],
      bg: 'rainy',
      voice: 0
    };
    if (type === 'choice') {
      n.choices = [{ label: '选项 A', to: '' }, { label: '选项 B', to: '' }];
    }
    if (type === 'ending') n.endKind = 'true';
    nodes.push(n);
    render();
    selectNode(n.id);
    drawWires();
    toast('已添加「' + TYPE_META[type].label + '」节点');
  }

  $('btnSave').addEventListener('click', () => { saveGraphToBackend(); });
  if ($('btnProduct')) $('btnProduct').addEventListener('click', openProductPanel);
  bindPack();
  bindDesignUi();
  bindSettings();
  // 主人 2026-11-08 定：新建就直接开空白，不再弹确认。
  // 旧项目一直留在后端和项目列表里，问这一句只是多一道点击。
  // ⚠ 这里必须绑工具栏那个独立按钮（btnNewProjectTb）。
  //   以前它和项目页的 #btnNewProject 撞了同一个 id，getElementById 只认第一个，
  //   于是两处监听全落在项目页按钮上 —— 点一次「新建」实际建出两个项目
  //   （实测：两个项目 created_at 相差 1 毫秒），而工具栏按钮点了没反应。
  if ($('btnNewProjectTb')) $('btnNewProjectTb').addEventListener('click', () => { newProject(); });

  $('btnAdd').addEventListener('click', (e) => {
    e.stopPropagation();
    const menu = $('addMenu');
    if (menu.classList.contains('show')) { menu.classList.remove('show'); return; }
    const r = e.currentTarget.getBoundingClientRect();
    const wr = wrap.getBoundingClientRect();
    menu.style.left = Math.round(r.left - wr.left) + 'px';
    menu.style.top = Math.round(r.bottom - wr.top + 6) + 'px';
    menu.classList.add('show');
  });

  $('addMenu').querySelectorAll('button[data-type]').forEach((b) => {
    b.addEventListener('click', (ev) => {
      ev.stopPropagation();
      $('addMenu').classList.remove('show');
      addNodeOfType(b.dataset.type);
    });
  });

  // 清空画布：不可恢复，所以先弹二次确认再动。
  // 为什么需要它：新项目里如果残留了示例/上一次的节点，点「开始生成」会因为
  // 项目已有内容而立刻结束，看起来像「1 秒就生成完了」，实际什么都没跑。
  const btnClearCanvas = $('btnClearCanvas');
  if (btnClearCanvas) {
    btnClearCanvas.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!nodes.length) { toast('画布已经是空的'); return; }
      popconfirm(
        btnClearCanvas,
        `确定清空画布上全部 ${nodes.length} 个节点吗？此操作不可恢复。`
          + '（项目里的图片、语音文件不会被删，只清空剧情节点）',
        async () => {
          nodes.length = 0;
          links.length = 0;
          selId = null;
          selLink = null;
          render();
          fitView();
          try {
            await saveGraphToBackend(true);
            toast('画布已清空并保存');
          } catch (err) {
            toast('画布已清空，但保存失败：' + ((err && err.message) || err));
          }
        },
        '确认清空',
      );
    });
  }

  /* ---------------- 选中 / 属性面板 ---------------- */
  /* ---------------- 节点属性面板 ----------------
     数据源一律用 rawById[id]（后端下发的原始 Node），不用画布上的精简副本：
     画布对象把多句台词并成一句、把 chars 整个丢了，拿它当编辑源会把图改坏。 */
  const TYPE_LABEL = { start: '序章', scene: '场景', choice: '分支', ending: '结局' };
  const END_LABEL = { true: '真结局', good: '好结局', bad: '坏结局', alt: '分歧结局' };
  const POS_LABEL = { left: '站左边', center: '站中间', right: '站右边' };
  const STANCES = [['stand', '站'], ['sit', '坐'], ['lean', '靠'], ['lie', '躺'], ['walk', '走']];

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function rawOf(id) {
    const live = nodes.find((x) => x.id === id);
    return rawById[id] || (live && live._raw) || null;
  }
  function imgTag(rel, cls, zoom) {
    if (!rel) return '<span class="ins-nopic">无图</span>';
    let url = '';
    try { url = GalAPI.assetURL(projectId, rel); } catch (_) { url = ''; }
    if (!url) return '<span class="ins-nopic">无图</span>';
    // 图还没生成 / 项目没绑定时 onerror 自己藏起来，别留一个破图占位
    return '<img class="' + cls + '" src="' + esc(url) + '" alt="" loading="lazy" ' +
      (zoom ? 'data-act="zoom-img" data-url="' + esc(url) + '" title="点击看大图" ' : '') +
      'onerror="this.style.display=\'none\'">';
  }

  /** 立绘预览：宽高都不写死，交给 CSS 按图片自身比例自适应（竖图 768×1344 也能看全） */
  function poseImg(rel) {
    if (!rel) return '<div class="ins-nopic tall">无立绘</div>';
    let url = '';
    try { url = GalAPI.assetURL(projectId, rel); } catch (_) { url = ''; }
    if (!url) return '<div class="ins-nopic tall">无立绘</div>';
    return '<img class="ins-pose" src="' + esc(url) + '" alt="" loading="lazy" ' +
      'data-act="zoom-img" data-url="' + esc(url) + '" title="点击看大图" ' +
      'onerror="this.style.display=\'none\'">';
  }
  function charOptions(sel) {
    const list = Array.isArray(graphCharacters) ? graphCharacters : [];
    const out = ['<option value="">（旁白 / 不出场）</option>'];
    list.forEach((c) => {
      out.push('<option value="' + esc(c.id) + '"' + (c.id === sel ? ' selected' : '') + '>' +
        esc(c.name || c.id) + '</option>');
    });
    if (sel && !list.some((c) => c.id === sel)) {
      out.push('<option value="' + esc(sel) + '" selected>' + esc(sel) + '（不在角色表）</option>');
    }
    return out.join('');
  }

  /* ================= 属性面板：按节点类型分块 =================
     不同类型给不同的区块顺序和强调色，让用户一眼看出这个节点该看哪几块：
       序章：基本信息 · 开场画面 · 台词 · 其它
       场景：基本信息 · 画面（背景/人物）· 台词 · 其它
       分支：基本信息 · 分支类型 · 选项与目标 · 画面 · 分支上的台词 · 其它
       结局：基本信息 · 结局类型 · 收尾台词 · 画面 · 其它
     区块顺序写在一张表里，加类型/调顺序只动这张表。 */
  const INS_ORDER = {
    start: ['basic', 'visual', 'lines', 'other'],
    scene: ['basic', 'visual', 'lines', 'other'],
    choice: ['basic', 'branch', 'choices', 'visual', 'lines', 'other'],
    ending: ['basic', 'end', 'lines', 'visual', 'other']
  };
  const INS_TITLES = {
    basic: '基本信息', visual: '画面', lines: '台词', other: '其它',
    branch: '分支类型', choices: '选项与目标', end: '结局类型'
  };
  const INS_VISUAL_TITLE = { start: '开场画面', scene: '画面 · 背景与人物', choice: '画面 · 背景与人物', ending: '画面 · 背景与人物' };
  const INS_LINES_TITLE = { start: '台词', scene: '台词', choice: '分支上的台词', ending: '收尾台词' };
  const INS_ICON = { basic: '基', visual: '画', lines: '词', other: '…', branch: '类', choices: '选', end: '终' };

  function insBlock(key, title, body, meta) {
    return '<section class="ins-block" data-blk="' + key + '" id="ins-blk-' + key + '">' +
      '<div class="ins-block-h"><span class="ico">' + esc(INS_ICON[key] || '·') + '</span>' +
      '<span class="t">' + esc(title) + '</span>' +
      (meta ? '<span class="meta">' + meta + '</span>' : '') + '</div>' +
      '<div class="ins-block-b">' + body + '</div></section>';
  }

  function renderInspector(live) {
    const raw = rawOf(live.id) || {};
    const type = live.type;
    const bg = raw.bg || {};
    const lines = (Array.isArray(raw.lines) && raw.lines.length)
      ? raw.lines : [{ who: '', text: '', voice: null, dur: 1 }];
    const chars = Array.isArray(raw.chars) ? raw.chars : [];
    if (!Array.isArray(raw.choices)) raw.choices = [];
    if (type === 'choice') {
      // 契约是 2~4 个选项：至少给两条可编辑行；画布上已经连了线的出口
      // （比如用户直接在 D 口连了线而 raw 里只有 2 条）也必须补出编辑行。
      const linkedMax = links.filter((l) => l.from === live.id)
        .reduce((m, l) => Math.max(m, l.port || 0), -1);
      const want = Math.max(CHOICE_MIN, raw.choices.length, linkedMax + 1);
      while (raw.choices.length < want && raw.choices.length < CHOICE_MAX) {
        raw.choices.push({
          label: '选项 ' + String.fromCharCode(65 + raw.choices.length),
          to: ''
        });
      }
    }
    const choices = raw.choices;
    const blocks = {};

    /* ---- 基本信息 ---- */
    blocks.basic =
      '<div class="field"><label>标题</label>' +
      '<input data-f="title" value="' + esc(live.title) + '"></div>' +
      (type !== 'start'
        ? '<div class="field"><label>节点类型 <span class="range-hint">换类型会清空出口，要重连</span></label>' +
          '<select data-f="__type">' +
          ['scene', 'choice', 'ending'].map((t) => '<option value="' + t + '"' +
            (t === type ? ' selected' : '') + '>' + TYPE_LABEL[t] + '</option>').join('') +
          '</select></div>'
        : '<div class="ins-dim">序章是故事的固定起点，类型不可改</div>') +
      '<div class="ins-kv"><span>编号</span><code>' + esc(live.idx || '—') + '</code>' +
      '<span>状态</span><code>' + esc(live.status || '—') + '</code></div>';

    /* ---- 画面：背景 + 人物（位置 / 姿态 / 情绪都挂在这里） ---- */
    let visualBody =
      '<div class="ins-sub"><div class="ins-sub-h">背景' +
      '<button class="ins-mini" data-act="pick-bg">从素材库挑</button></div>' +
      '<div class="ins-asset">' + imgTag(bg.asset, 'ins-thumb', true) +
      '<div class="ins-asset-main">' +
      '<div class="ins-asset-path">' + esc(bg.asset || '（还没生成）') + '</div>' +
      '<div class="ins-row">' +
      '<input data-f="bg.place" placeholder="地点" value="' + esc(bg.place || '') + '">' +
      '<input data-f="bg.time" placeholder="时间" value="' + esc(bg.time || '') + '">' +
      '<input data-f="bg.weather" placeholder="天气" value="' + esc(bg.weather || '') + '">' +
      '</div></div></div>' +
      '<textarea data-f="bg.prompt" rows="3" placeholder="这张背景的生图提示词">' +
      esc(bg.prompt || '') + '</textarea></div>';

    visualBody += '<div class="ins-sub"><div class="ins-sub-h">人物 · 立绘' +
      '<span class="ins-sub-n">' + chars.length + ' 人</span>' +
      '<button class="ins-mini" data-act="char-add">+ 加一个人</button></div>';
    if (!chars.length) {
      visualBody += '<div class="ins-dim">这个节点没有任何立绘（只有旁白时是正常的）</div>';
    }
    chars.forEach((c, i) => {
      const ch = (Array.isArray(graphCharacters) ? graphCharacters : [])
        .filter((x) => x.id === c.id)[0] || {};
      visualBody += '<div class="ins-char">' +
        '<div class="ins-char-head"><span class="ins-char-idx">' + (i + 1) + '</span>' +
        '<span class="ins-char-who">' + esc(ch.name || c.id || '未选角色') + '</span>' +
        '<span class="ins-dim">' + esc(c.pose || '无立绘') + '</span></div>' +
        '<div class="ins-char-pic">' + poseImg(c.pose) + '</div>' +
        '<div class="ins-char-main">' +
        '<select data-f="chars.' + i + '.id">' + charOptions(c.id) + '</select>' +
        '<div class="ins-row">' +
        '<select data-f="chars.' + i + '.pos">' +
        ['left', 'center', 'right'].map((p) => '<option value="' + p + '"' +
          (p === (c.pos || 'center') ? ' selected' : '') + '>' + POS_LABEL[p] + '</option>').join('') +
        '</select>' +
        '<select data-f="chars.' + i + '.stance">' +
        STANCES.map(([v, label]) => '<option value="' + v + '"' +
          (v === (c.stance || 'stand') ? ' selected' : '') + '>' + label + '</option>').join('') +
        '</select></div>' +
        '<input data-f="chars.' + i + '.mood" placeholder="情绪（平静 / 紧张 / 勉强笑 …）" value="' +
        esc(c.mood || '') + '">' +
        '<div class="ins-line-foot"><span></span><span>' +
        '<button class="ins-mini" data-act="pick-pose" data-i="' + i + '">换图</button>' +
        '<button class="ins-mini" data-act="char-del" data-i="' + i + '">移除</button>' +
        '</span></div></div></div>';
    });
    visualBody += '</div>';
    blocks.visual = visualBody;

    /* ---- 台词（分支节点是「分支上的台词」，结局是「收尾台词」） ---- */
    let linesBody = '<div class="ins-sub-h">共 ' + lines.length + ' 句' +
      '<button class="ins-mini" data-act="line-add">+ 加一句</button></div>';
    lines.forEach((l, i) => {
      const dur = (l.dur > 0) ? l.dur.toFixed(2) + 's' : '—';
      const voice = l.voice
        ? '<button class="ins-mini" data-act="play" data-i="' + i + '">▶ ' + dur + '</button>'
        : '<span class="ins-dim">无语音</span>';
      linesBody += '<div class="ins-line">' +
        '<div class="ins-line-top"><span class="ins-line-no">' + (i + 1) + '</span>' +
        '<select data-f="lines.' + i + '.who">' + charOptions(l.who) + '</select></div>' +
        '<textarea data-f="lines.' + i + '.text" rows="2" placeholder="这一句显示的台词">' +
        esc(l.text) + '</textarea>' +
        '<div class="ins-line-foot">' + voice +
        '<span><button class="ins-mini" data-act="pick-voice" data-i="' + i + '">换语音</button>' +
        '<button class="ins-mini" data-act="line-del" data-i="' + i + '">删这句</button></span></div>' +
        '</div>';
    });

    // 角色音色：角色级设置，改了它这个角色所有台词都变
    const speaking = Array.from(new Set(lines.map((l) => l.who).filter(Boolean)));
    if (speaking.length) {
      linesBody += '<div class="ins-sub"><div class="ins-sub-h">角色音色' +
        '<span class="ins-sub-n">角色级</span></div>';
      speaking.forEach((who) => {
        const ch = (Array.isArray(graphCharacters) ? graphCharacters : [])
          .filter((c) => c.id === who)[0] || {};
        linesBody += '<div class="ins-row"><span class="ins-dim ins-who-fixed">' +
          esc(ch.name || who) + '</span>' +
          '<input data-f="__voice.' + esc(who) + '" list="voicePresets" value="' + esc(ch.voice_id || '') +
          '" placeholder="Qwen-TTS 音色名，如 Cherry"></div>';
      });
      linesBody += '<div class="ins-dim">音色属于角色，不在单个节点上 —— 改了一次，这个角色所有台词都跟着变</div></div>';
    }
    blocks.lines = linesBody;

    /* ---- 分支类型（必要 / 气氛） ---- */
    if (type === 'choice') {
      const bk = raw.branch_kind || 'key';
      blocks.branch =
        '<div class="ins-fieldset">' +
        '<label class="ins-opt"><input type="radio" name="insBranch" value="key"' +
        (bk === 'key' ? ' checked' : '') + ' data-f="branch_kind">' +
        '<span><b>必要分支</b><em>决定进哪个结局，分支两侧的剧情都要成立</em></span></label>' +
        '<label class="ins-opt"><input type="radio" name="insBranch" value="flavor"' +
        (bk === 'flavor' ? ' checked' : '') + ' data-f="branch_kind">' +
        '<span><b>气氛分支</b><em>只是多一句反应，之后汇合回主线</em></span></label>' +
        '</div>' +
        '<div class="ins-dim">必要分支会被计入结局结算；气氛分支不改变走向。</div>';
    }

    /* ---- 选项与目标（2~4 条，空目标自动忽略） ---- */
    if (type === 'choice') {
      let cb = '';
      choices.forEach((c, i) => {
        const opts = ['<option value="">（未连）</option>'].concat(
          nodes.filter((x) => x.id !== live.id).map((x) => '<option value="' + esc(x.id) + '"' +
            (x.id === c.to ? ' selected' : '') + '>' + esc(x.id) + ' · ' +
            esc((x.title || '').slice(0, 12)) + '</option>'));
        cb += '<div class="ins-choice">' +
          '<div class="ins-choice-h"><span class="ins-choice-port">' +
          String.fromCharCode(65 + i) + '</span>' +
          '<span class="ins-dim">出口 ' + (i + 1) + '</span>' +
          (choices.length > CHOICE_MIN
            ? '<button class="ins-mini" data-act="choice-del" data-i="' + i + '">删</button>'
            : '') + '</div>' +
          '<input data-f="choices.' + i + '.label" placeholder="选项文字" value="' + esc(c.label) + '">' +
          '<select data-f="choices.' + i + '.to">' + opts.join('') + '</select></div>';
      });
      cb += '<div class="ins-line-foot"><span class="ins-dim">2~4 个选项，没连目标的出口会被自动忽略</span>' +
        (choices.length < CHOICE_MAX
          ? '<button class="ins-mini" data-act="choice-add">+ 加一个选项</button>'
          : '<span class="ins-dim">已满 ' + CHOICE_MAX + ' 个</span>') + '</div>';
      blocks.choices = cb;
    }

    /* ---- 结局类型 ---- */
    if (type === 'ending') {
      const ek = raw.end_kind || 'true';
      const END_DESC = {
        true: '真正的收尾：主线走完才有',
        good: '好结局：代价小、留希望',
        bad: '坏结局：失败或错过的分支',
        alt: '分歧结局：隐藏/另一条路'
      };
      blocks.end =
        '<div class="ins-fieldset">' +
        Object.keys(END_LABEL).map((k) => '<label class="ins-opt"><input type="radio" name="insEnd" value="' +
          k + '"' + (k === ek ? ' checked' : '') + ' data-f="end_kind">' +
          '<span><b>' + END_LABEL[k] + '</b><em>' + END_DESC[k] + '</em></span></label>').join('') +
        '</div>';
    }

    /* ---- 其它：状态 / 来源 / 时长 ---- */
    const durAll = lines.reduce((s, l) => s + (Number(l.dur) || 0), 0);
    blocks.other =
      '<div class="ins-kv"><span>时长</span><code>' + (durAll > 0 ? durAll.toFixed(1) + 's' : '—') + '</code>' +
      '<span>背景来源</span><code>' + esc(bg.provider || '—') + '</code></div>' +
      '<div class="ins-kv"><span>节点 ID</span><code>' + esc(live.id) + '</code></div>' +
      '<div class="ins-dim">字段改动会自动保存到后端；保存失败时工具栏会变成「保存失败」。</div>';

    /* ---- 组装：按类型顺序 + 顶部快捷跳转 ---- */
    const order = INS_ORDER[type] || INS_ORDER.scene;
    const h = [];
    h.push('<div class="ins-node"><code>' + esc(live.id) + '</code>' +
      '<span class="ins-kind kind-' + esc(type) + '">' + (TYPE_LABEL[type] || type) + '</span></div>');

    h.push('<div class="ins-chips">' + order.map((k, n) =>
      '<button type="button" class="ins-chip" data-act="goto" data-blk="' + k + '">' +
      '<span class="n">' + (n + 1) + '</span>' + esc(INS_TITLES[k]) + '</button>').join('') + '</div>');

    order.forEach((k) => {
      if (!blocks[k]) return;
      let title = INS_TITLES[k];
      if (k === 'visual') title = INS_VISUAL_TITLE[type] || title;
      if (k === 'lines') title = INS_LINES_TITLE[type] || title;
      const metaMap = {
        visual: (bg.asset ? '有图' : '待生成') + ' · ' + chars.length + ' 人',
        lines: lines.length + ' 句',
        choices: choices.length + ' 项'
      };
      h.push(insBlock(k, title, blocks[k], metaMap[k] || ''));
    });

    $('insFields').innerHTML = h.join('');
    $('insFields').dataset.type = type;
    $('insTitle').textContent = live.title || live.id;   // 类型已经由徽章表达，标题别重复拼
    $('insTip').hidden = true;
  }

  /* ---------------- 素材选择浮层（背景 / 立绘 / 语音都从这里挑） ---------------- */
  let libraryCache = null;
  let libraryOverride = null;      // 验收用：注入一份假素材库，免得依赖真后端
  let pickerResolve = null;
  const PICKER_TITLE = { background: '挑一张背景', character: '挑一张立绘', voice: '挑一条语音' };

  async function loadLibrary() {
    if (libraryOverride) return libraryOverride;
    if (libraryCache) return libraryCache;
    if (!projectId) return null;
    try {
      const res = await GalAPI.getLibrary(projectId);
      if (res && res.ok) libraryCache = res;
    } catch (_) { libraryCache = null; }
    return libraryCache;
  }
  function clearLibraryCache() { libraryCache = null; }

  function openPicker(kind) {
    clearLibraryCache();          // 每次打开都拉最新的：素材库那边上传完不用手动同步
    return loadLibrary().then((lib) => {
      if (!lib) { toast('还没绑定项目，先生成或打开一个项目'); return null; }
      const list = (lib[kind] || []).slice();
      $('pickerTitle').textContent = PICKER_TITLE[kind] || '选择素材';
      $('pickerCount').textContent = list.length + ' 个';
      const grid = $('pickerGrid');
      if (!list.length) {
        grid.innerHTML = '<div class="picker-empty">素材库里还没有' +
          (kind === 'voice' ? '语音' : '图片') + ' —— 先去「本项目素材库」上传，或让 agent 生成一轮</div>';
      } else {
        grid.innerHTML = list.map((it, i) => {
          const vis = kind === 'voice'
            ? '<div class="picker-aud">▶</div>'
            : '<img src="' + esc(GalAPI.assetURL(projectId, it.asset)) + '" alt="" loading="lazy" ' +
              'onerror="this.style.opacity=.18">';
          return '<button type="button" class="picker-item" data-i="' + i + '">' + vis +
            '<div class="nm" title="' + esc(it.name) + '">' + esc(it.name) + '</div></button>';
        }).join('');
      }
      $('pickerMask').hidden = false;
      return new Promise((resolve) => {
        pickerResolve = (item) => {
          $('pickerMask').hidden = true;
          pickerResolve = null;
          resolve(item);
        };
        grid.onclick = (ev) => {
          const btn = ev.target.closest('.picker-item');
          if (!btn || !pickerResolve) return;
          pickerResolve(list[Number(btn.dataset.i)] || null);
        };
      });
    });
  }

  $('pickerClose').addEventListener('click', () => { if (pickerResolve) pickerResolve(null); });
  $('pickerMask').addEventListener('click', (ev) => {
    if (ev.target === $('pickerMask') && pickerResolve) pickerResolve(null);
  });

  function setInsState(text) {
    const el = document.querySelector('[data-state]');
    if (el) { el.textContent = text; el.dataset.state = text; }
  }

  function selectNode(id) {
    selId = id;
    const n = nodes.find((x) => x.id === id);
    if (!n) return;
    document.querySelectorAll('.node').forEach((el) => el.classList.toggle('sel', el.dataset.id === id));
    renderInspector(n);
    $('inspector').classList.add('show');
  }

  /** 节点类型热切换。
      三类节点的出口数不同（scene 1 / choice 3 / ending 0），所以换类型等于换一个
      LiteGraph 节点类 —— 直接改 nodes[].type 再 render() 全量重载最稳（render 会走
      GalEditor.load）。原有出边按新类型的出口数重连，先清掉免得留下非法连线。 */
  function changeNodeType(id, newType) {
    const live = nodes.find((x) => x.id === id);
    if (!live || live.type === newType) return;
    if (!TYPE_LABEL[newType]) return;
    const oldType = live.type;
    pushHistory('切换节点类型');
    live.type = newType;
    links = links.filter((l) => l.from !== id);      // 出口数变了，旧出边作废
    let raw = rawById[id];
    if (!raw) { raw = nodeSkeleton(live); rawById[id] = raw; }
    raw.type = newType;
    if (newType === 'ending') {
      if (!raw.end_kind) raw.end_kind = 'good';
      delete raw.choices;
      delete raw.branch_kind;
    } else {
      delete raw.end_kind;
      if (newType === 'choice') {
        if (raw.branch_kind !== 'flavor') raw.branch_kind = 'key';
        if (!Array.isArray(raw.choices)) raw.choices = [];
      } else {
        delete raw.branch_kind;
      }
    }
    render();
    selectNode(id);
    addLog('节点 ' + id + ' 类型：' + (TYPE_LABEL[oldType] || oldType) +
      ' → ' + (TYPE_LABEL[newType] || newType) + '（出口已清空，需要重新连）');
    toast('已改成「' + (TYPE_LABEL[newType] || newType) + '」，它的出口要重新连');
    scheduleAutosave();
  }

  /** 取节点原始数据（没有就现场补一份骨架，保证面板改动总能落地） */
  function ensureRaw(live) {
    let raw = rawById[live.id];
    if (!raw) { raw = nodeSkeleton(live); rawById[live.id] = raw; }
    return raw;
  }

  /** 台词 / 立绘改了以后，把「卡片上显示的那一句」同步回 live + 画布
      —— 以前只改 raw，卡片要等下次重载才更新，看起来像没保存。 */
  function syncNodeSummary(live, raw) {
    const ls = Array.isArray(raw.lines) ? raw.lines : [];
    live.who = (ls[0] && ls[0].who) || '';
    live.whoName = charName(live.who);
    live.text = ls.map((l) => l.text).filter((t) => t != null && t !== '').join(' ') || '…';
    live.voice = ls.reduce((s, l) => s + (Number(l.dur) || 0), 0);
    live.bgAsset = (raw.bg && raw.bg.asset) || live.bgAsset || '';
    live.charAsset = (Array.isArray(raw.chars) && raw.chars[0] && raw.chars[0].pose) || '';
    GalEditor.updateNode(live.id, {
      who: live.who, whoName: live.whoName, text: live.text, voice: live.voice,
      bgAsset: live.bgAsset, charAsset: live.charAsset
    });
  }

  /** 从面板写回节点：字段路径形如 'bg.prompt' / 'lines.2.text' / 'chars.0.pos' */
  function onInsInput(e) {
    const el = e.target;
    const field = el && el.dataset && el.dataset.f;
    if (!field || !selId) return;
    const live = nodes.find((x) => x.id === selId);
    if (!live) return;
    if (field === '__type') { changeNodeType(selId, el.value); return; }
    // 角色音色：改的是角色表（全局生效），不是节点
    if (field.indexOf('__voice.') === 0) {
      const who = field.slice('__voice.'.length);
      const ch = (Array.isArray(graphCharacters) ? graphCharacters : [])
        .filter((c) => c.id === who)[0];
      if (ch) {
        ch.voice_id = el.value.trim();
        addLog('角色 ' + who + ' 音色 → ' + (ch.voice_id || '默认'));
        scheduleAutosave();
      }
      return;
    }
    // 结局类型：raw 是权威（保存时 base.end_kind 以它为准），同时同步到画布徽标
    if (field === 'end_kind') {
      const raw = ensureRaw(live);
      raw.end_kind = el.value;
      live.endKind = el.value;
      GalEditor.updateNode(live.id, { endKind: el.value });
      renderInspector(live);
      scheduleAutosave();
      return;
    }
    // 分支性质：只对 choice 有意义，卡片底部会显示「必要 / 气氛」
    if (field === 'branch_kind') {
      const raw = ensureRaw(live);
      raw.branch_kind = el.value === 'flavor' ? 'flavor' : 'key';
      live.branchKind = raw.branch_kind;
      GalEditor.updateNode(live.id, { branchKind: raw.branch_kind });
      renderInspector(live);
      scheduleAutosave();
      return;
    }
    // 分支选项同时要写画布对象：buildGraphPayload 是从 n.choices 重建 base.choices 的，
    // 只改 rawById 会在保存时被画布上的旧值盖掉。
    if (field.indexOf('choices.') === 0) {
      const seg = field.split('.');
      const k = Number(seg[1]);
      const key = seg[2];
      let raw0 = ensureRaw(live);
      if (!Array.isArray(raw0.choices)) raw0.choices = [];
      live.choices = Array.isArray(live.choices) ? live.choices : [];
      // 数组补密：稀疏位写进 JSON 会变成 null，后端会当脏数据
      while (raw0.choices.length <= k) {
        raw0.choices.push({
          label: '选项 ' + String.fromCharCode(65 + raw0.choices.length), to: ''
        });
      }
      while (live.choices.length <= k) {
        const src = raw0.choices[live.choices.length] || {};
        live.choices.push({ label: src.label, to: src.to || '' });
      }
      raw0.choices[k] = Object.assign({}, raw0.choices[k], { [key]: el.value });
      live.choices[k] = Object.assign({}, live.choices[k], { [key]: el.value });
      if (key === 'to') {
        // 连线跟着走：这个出口原来的边作废，换成新的
        links = links.filter((l) => !(l.from === selId && (l.port || 0) === k));
        if (el.value && el.value !== selId) {
          links.push({ id: 'l' + Date.now() + k, from: selId, port: k, to: el.value });
        }
        render();
        selectNode(selId);
        addLog('节点 ' + selId + ' 第 ' + (k + 1) + ' 条分支 → ' + (el.value || '（断开）'));
      } else {
        GalEditor.updateNode(selId, { choiceLabels: live.choices.map((c) => c.label || '') });
      }
      scheduleAutosave();
      return;
    }
    if (field === 'title') {
      live.title = el.value;
      $('insTitle').textContent = live.title || live.id;
      GalEditor.updateNode(live.id, { title: live.title });
      scheduleAutosave();
      return;
    }
    const raw = ensureRaw(live);
    const parts = field.split('.');
    let box = raw;
    for (let i = 0; i < parts.length - 1; i += 1) {
      const k = /^\d+$/.test(parts[i]) ? Number(parts[i]) : parts[i];
      if (box[k] == null) box[k] = /^\d+$/.test(parts[i + 1]) ? [] : {};
      box = box[k];
    }
    const last = parts[parts.length - 1];
    box[/^\d+$/.test(last) ? Number(last) : last] = el.value;
    // 台词 / 立绘 / 背景资源改了 → 卡片摘要跟着变（不重排面板，免得正在输入的光标丢）
    if (parts[0] === 'lines' || parts[0] === 'chars' || parts[0] === 'bg') {
      syncNodeSummary(live, raw);
    }
    // 说话人 / 立绘角色是下拉框（改完就结束输入），说话人变了「角色音色」那块要跟着换人，
    // 所以这两种改动可以安全地整块重排一次（文本框才需要保住光标）。
    if ((parts[0] === 'lines' && parts[2] === 'who') || (parts[0] === 'chars' && parts[2] === 'id')) {
      renderInspector(live);
    }
    scheduleAutosave();
  }

  async function onInsClick(e) {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const act = btn.dataset.act;
    // 这两个不依赖选中节点
    if (act === 'zoom-img') { openInsZoom(btn.dataset.url); return; }
    if (act === 'goto') {
      const blk = document.getElementById('ins-blk-' + btn.dataset.blk);
      const body = $('insBody');
      if (blk && body) body.scrollTo({ top: blk.offsetTop - body.offsetTop - 6, behavior: 'smooth' });
      return;
    }
    if (!selId) return;
    const i = Number(btn.dataset.i);
    const live = nodes.find((x) => x.id === selId);
    if (!live) return;
    const raw = ensureRaw(live);

    if (act === 'play') {
      const line = (raw.lines || [])[i];
      if (line && line.voice) new Audio(GalAPI.assetURL(projectId, line.voice)).play();
      return;
    }
    if (act === 'pick-bg') {
      const item = await openPicker('background');
      if (!item || selId !== live.id) return;
      raw.bg = Object.assign({}, raw.bg, { asset: item.asset, provider: 'library' });
      live.bgAsset = item.asset;
      GalEditor.updateNode(live.id, { bgAsset: item.asset });
      renderInspector(live); scheduleAutosave();
      addLog('节点 ' + live.id + ' 背景换成素材库里的 ' + item.name);
      return;
    }
    if (act === 'pick-pose') {
      const item = await openPicker('character');
      if (!item || selId !== live.id || !(raw.chars || [])[i]) return;
      raw.chars[i].pose = item.asset;
      syncNodeSummary(live, raw);
      renderInspector(live); scheduleAutosave();
      return;
    }
    if (act === 'pick-voice') {
      const item = await openPicker('voice');
      if (!item || selId !== live.id || !(raw.lines || [])[i]) return;
      raw.lines[i].voice = item.asset;
      renderInspector(live); scheduleAutosave();
      return;
    }
    if (act === 'choice-add') {
      if (!Array.isArray(raw.choices)) raw.choices = [];
      if (raw.choices.length >= CHOICE_MAX) { toast('分支最多 ' + CHOICE_MAX + ' 个选项'); return; }
      raw.choices.push({ label: '选项 ' + String.fromCharCode(65 + raw.choices.length), to: '' });
      renderInspector(live);
      scheduleAutosave();
      addLog('节点 ' + live.id + ' 加了第 ' + raw.choices.length + ' 个选项');
      return;
    }
    if (act === 'choice-del') {
      if (!Array.isArray(raw.choices) || raw.choices.length <= CHOICE_MIN) {
        toast('分支至少要留 ' + CHOICE_MIN + ' 个选项');
        return;
      }
      if (!raw.choices[i]) return;
      raw.choices.splice(i, 1);
      // 出口编号连续：删掉第 i 个之后，i 之后的出边整体前移一格
      const kept = [];
      links.forEach((l) => {
        if (l.from !== live.id) { kept.push(l); return; }
        const port = l.port || 0;
        if (port === i) return;
        kept.push(port > i ? Object.assign({}, l, { port: port - 1 }) : l);
      });
      links = kept;
      live.choices = raw.choices.map((c, k) => {
        const lk = links.filter((l) => l.from === live.id && (l.port || 0) === k)[0];
        return { label: c.label, to: lk ? lk.to : (c.to || '') };
      });
      render();
      selectNode(live.id);
      scheduleAutosave();
      addLog('节点 ' + live.id + ' 删掉第 ' + (i + 1) + ' 个选项');
      return;
    }
    if (act === 'line-add') {
      raw.lines = Array.isArray(raw.lines) ? raw.lines : [];
      raw.lines.push({ who: '', text: '…', voice: null, dur: 1 });
    } else if (act === 'line-del' && (raw.lines || []).length > 1) {
      raw.lines.splice(i, 1);
    } else if (act === 'char-add') {
      raw.chars = Array.isArray(raw.chars) ? raw.chars : [];
      const first = (graphCharacters && graphCharacters[0]) || {};
      raw.chars.push({
        id: first.id || '', pose: first.ref_image || '',
        pos: 'center', mood: '平静', stance: 'stand'
      });
    } else if (act === 'char-del') {
      if (!(raw.chars || [])[i]) return;
      raw.chars.splice(i, 1);
    } else {
      return;
    }
    syncNodeSummary(live, raw);
    renderInspector(live);
    scheduleAutosave();
  }

  $('insFields').addEventListener('input', onInsInput);
  $('insFields').addEventListener('change', onInsInput);
  $('insFields').addEventListener('click', onInsClick);

  /** 新版画布（LiteGraph）点节点时填充右侧检查器。
      ⚠ 历史上这里引用了一个从未定义的 fillInspector：
         `typeof fillInspector === 'function'` 恒为 false → 静默跳过 →
         右侧属性面板永远弹不出来，但也不报错，谁都发现不了。
      现在统一走 selectNode（它负责填充 + 设 selId + 加 show）。 */
  function fillInspector(n) {
    if (n) selectNode(n.id);
  }

  /** 双击节点台词 → 就地编辑（Esc 取消 / Ctrl+Enter 保存 / 失焦保存） */
  function startInlineEdit(el, n) {
    if (el.querySelector('.inline-edit')) return;
    const lineEl = el.querySelector('.line');
    if (!lineEl) return;
    const before = snap();
    const ta = document.createElement('textarea');
    ta.className = 'inline-edit';
    ta.value = n.text || '';
    lineEl.replaceWith(ta);
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);

    let done = false;
    const finish = (save) => {
      if (done) return;
      done = true;
      if (save) {
        n.text = ta.value;
        if (snap() !== before) {
          pushHistory('编辑台词', before);
          addLog('改台词：节点 ' + n.idx);
        }
      }
      el.innerHTML = nodeHTML(n);
      bindPorts(el, n);
      drawWires();
      if (selId === n.id) selectNode(n.id);
    };

    ta.addEventListener('blur', () => finish(true));
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); finish(false); }
      else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); finish(true); }
    });
  }

  $('insClose').addEventListener('click', () => {
    $('inspector').classList.remove('show');
    selId = null;
    document.querySelectorAll('.node').forEach((el) => el.classList.remove('sel'));
  });

  /* 图片看大图：点属性面板里的背景 / 立绘预览，弹一个原图浮层。
     面板宽度有限（360px），立绘再大也只能到 300 多，看不清细节时就靠这个。 */
  const insZoom = $('insZoom');
  function openInsZoom(url) {
    if (!url || !insZoom) return;
    $('insZoomImg').src = url;
    insZoom.hidden = false;
  }
  function closeInsZoom() {
    if (!insZoom || insZoom.hidden) return;
    insZoom.hidden = true;
    $('insZoomImg').removeAttribute('src');
  }
  if (insZoom) {
    insZoom.addEventListener('click', (e) => {
      if (e.target === insZoom || (e.target.closest && e.target.closest('#insZoomClose'))) closeInsZoom();
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeInsZoom();
  });

  /* 旧版画布的 fixed-id 面板同步（insWho/insText/insBg）已经废弃：
     那几个 input 不存在了，留着会在初始化时 addEventListener(null) 直接抛错，
     把后面所有绑定全带挂。属性面板现在统一走 onInsInput / renderInspector。 */

  function bindPorts(el, n) {
    el.querySelectorAll('.port.out').forEach((p) => {
      p.addEventListener('pointerdown', (e) => {
        e.stopPropagation();
        e.preventDefault();
        startLinking(n.id, parseInt(p.dataset.port, 10) || 0, e);
      });
    });
  }

  let regenerating = false;
  $('btnRegen').addEventListener('click', async () => {
    const n = nodes.find((x) => x.id === selId);
    if (!n || regenerating) return;

    // 模拟模式没有后端可调，本地编一个时长（界面上会明说是模拟）
    if (GalAPI.mode === 'mock') {
      setInsState('生成中…');
      $('btnRegen').disabled = true;
      addLog('（模拟数据）重新生成节点 ' + n.idx);
      setTimeout(() => {
        n.voice = +(3 + Math.random() * 3).toFixed(1);
        render();
        selectNode(n.id);
        setInsState('已生成');
        $('btnRegen').disabled = false;
        addLog('节点 ' + n.idx + ' 完成（模拟）');
        toast('节点已重新生成（模拟）');
      }, 900);
      return;
    }

    if (!projectId) {
      addLog('✖ 还没绑定项目：先点一次「开始生成剧情」，或在真后端模式下等它恢复上次项目');
      toast('还没有项目，先点一次「开始生成剧情」');
      return;
    }

    regenerating = true;
    $('btnRegen').disabled = true;
    $('btnRegen').textContent = '生成中…';
    setInsState('生成中…');
    addLog('重生成节点 ' + n.idx + '：背景 / 立绘 / 语音');

    const r = await GalAPI.regenerateNode(projectId, n.id, ['bg', 'char', 'voice']);
    if (!r || !r.ok) {
      const d = (r && r.detail) || '未知错误';
      addLog('✖ 重生成失败：' + d);
      toast('重生成失败：' + d);
      setInsState('失败');
      $('btnRegen').disabled = false;
      $('btnRegen').textContent = '重新生成本节点';
      regenerating = false;
      return;
    }

    let patches = 0;
    const res = await GalAPI.watchJob(r.job_id, (type, ev) => {
      if (type === 'log') addLog('· ' + ev.text);
      if (type === 'node') patches++;
      if (type === 'error') addLog('✖ ' + (ev.message || ev.code));
    });

    // 整图拉回来最稳：这个节点的素材、台词、连线和 choices 会一起对齐
    await pullGraphFromBackend();
    const nn = nodes.find((x) => x.id === n.id);
    if (nn) {
      selectNode(nn.id);
      setInsState('已生成');
    }
    addLog((res && res.ok ? '✔ ' : '✖ ') + '节点 ' + n.idx + ' 重生成结束（' + patches + ' 个更新）');
    toast(res && res.ok ? '节点已重新生成' : '重生成中断');
    $('btnRegen').disabled = false;
    $('btnRegen').textContent = '重新生成本节点';
    regenerating = false;
  });

  function deleteSelected() {
    if (!selId) return;
    const n = nodes.find((x) => x.id === selId);
    if (!n) return;
    pushHistory('删除节点');
    nodes = nodes.filter((x) => x.id !== selId);
    links = links.filter((l) => l.from !== selId && l.to !== selId);
    selId = null;
    $('inspector').classList.remove('show');
    render();
    addLog('删除节点 ' + n.idx);
    toast('已删除（Ctrl+Z 可撤回）');
  }

  function duplicateSelected() {
    if (!selId) return;
    const n = nodes.find((x) => x.id === selId);
    if (!n) return;
    pushHistory('复制节点');
    const copy = JSON.parse(JSON.stringify(n));
    copy.id = newNodeId();
    copy.idx = String(nodes.length + 1).padStart(2, '0');
    copy.title = n.title + ' 副本';
    copy.x = n.x + 44;
    copy.y = n.y + 44;
    nodes.push(copy);
    render();
    selectNode(copy.id);
    drawWires();
    toast('已复制节点');
  }

  $('btnDel').addEventListener('click', deleteSelected);
  $('btnUndo').addEventListener('click', undo);
  $('btnRedo').addEventListener('click', redo);

  /* ---------------- Ren'Py 脚本预览（图的唯一真源 → .rpy） ---------------- */
  function bgName(n) {
    if (n.bgAsset) {
      const base = n.bgAsset.split('/').pop().replace(/\.[^.]+$/, '');
      return base.replace(/[^\w\u4e00-\u9fa5]/g, '_');
    }
    return n.bg || 'rainy';
  }

  function voiceOf(n) {
    if (n.voiceAsset) return n.voiceAsset;
    return 'voice/' + n.id + '_01.wav';
  }

  function toRenpy() {
    const out = [];
    out.push('# 由奇迹工坊自动生成 —— 请勿手改，改图后重新导出');
    out.push('# 角色暂用名字字符串；要头像/立绘差分时改成 Character("她", image="heroine")');
    out.push('');
    out.push('label start:');
    const start = nodes.find((x) => x.type === 'start') || nodes[0];
    out.push('    jump ' + (start ? start.id : 'n1'));
    out.push('');

    nodes.forEach((n) => {
      out.push('label ' + n.id + ':');
      out.push('    scene bg ' + bgName(n));
      if (n.charAsset) out.push('    show ' + n.id + '_char at center');

      const text = String(n.text || '').replace(/"/g, '\\"');
      if (n.voice > 0) out.push('    voice "' + voiceOf(n) + '"');
      if (n.who) out.push('    "' + n.who + '" "' + text + '"');
      else if (text) out.push('    "' + text + '"');

      if (n.type === 'choice' && n.choices && n.choices.length) {
        out.push('    menu:');
        n.choices.forEach((c) => {
          out.push('        "' + String(c.label).replace(/"/g, '\\"') + '":');
          out.push('            jump ' + c.to);
        });
      } else if (n.type === 'ending') {
        out.push('    # ' + (n.endKind === 'true' ? '真结局' : n.endKind === 'bad' ? '坏结局' : '隐藏结局'));
        out.push('    return');
      } else {
        const next = links.find((l) => l.from === n.id);
        out.push(next ? '    jump ' + next.to : '    return');
      }
      out.push('');
    });
    return out.join('\n');
  }

  let rpyDirty = true;

  function renderRenpy() {
    const pre = $('rpyPreview');
    if (!pre) return;
    pre.textContent = toRenpy();
    rpyDirty = false;
  }
  $('btnRpy').addEventListener('click', renderRenpy);

  /* ---------------- 键盘快捷键 ---------------- */
  document.addEventListener('keydown', (e) => {
    const t = e.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');
    const mod = e.ctrlKey || e.metaKey;
    const k = (e.key || '').toLowerCase();

    if (mod && k === 'z') {
      if (typing) return;
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
      return;
    }
    if (mod && k === 'y') {
      if (typing) return;
      e.preventDefault();
      redo();
      return;
    }
    if (mod && k === 'd') {
      if (typing) return;
      e.preventDefault();
      duplicateSelected();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
      e.preventDefault();
      saveGraphToBackend();
      return;
    }

    if ((e.key === 'Delete' || e.key === 'Backspace') && !typing) {
      if (selLink !== null) { e.preventDefault(); deleteLink(); return; }
      if (selId) { e.preventDefault(); deleteSelected(); }
      return;
    }
    if (e.key === 'Escape') {
      if ($('player').classList.contains('show')) { $('player').classList.remove('show'); return; }
      if (selId) {
        selId = null;
        $('inspector').classList.remove('show');
        render();
      }
    }
  });

  function tryLink(toId) {
    if (!pendingFrom) return;
    const from = pendingFrom.from;
    const port = pendingFrom.port;
    if (from === toId) { pendingFrom = null; drawWires(); return; }
    const dup = links.some((l) => l.from === from && l.port === port && l.to === toId);
    if (dup) { toast('这条线已经有了'); pendingFrom = null; drawWires(); return; }
    pushHistory('连线');
    links = links.filter((l) => !(l.from === from && l.port === port));
    links.push({ from: from, port: port, to: toId });
    pendingFrom = null;
    drawWires();
    toast('已连接 ' + from + ' → ' + toId);
    addLog('连线 ' + from + ' → ' + toId);
  }

  /* ---------------- toast / 日志 ---------------- */
  let toastTimer = null;
  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 1900);
  }
  function addLog(msg) {
    const d = new Date();
    const ts = [d.getHours(), d.getMinutes(), d.getSeconds()].map((x) => String(x).padStart(2, '0')).join(':');
    const box = $('logBox');
    box.textContent += '[' + ts + '] ' + msg + '\n';
    box.scrollTop = box.scrollHeight;
  }

  /** 生成日志默认收起，点标题展开 */
  function toggleLog(open) {
    const box = $('logBox');
    if (!box) return;
    const show = typeof open === 'boolean' ? open : box.style.display === 'none';
    box.style.display = show ? 'block' : 'none';
    const s = $('logState');
    if (s) s.textContent = show ? '收起' : '展开';
  }
  $('logToggle').addEventListener('click', () => toggleLog());

  // 顶栏状态跟卡片实时同步（数量不写死）
  function updateGlobalMeta() {
    const cards = Array.from(document.querySelectorAll('.api-card'));
    const ok = cards.filter((c) => c.querySelector('.dot.ok')).length;
    $('globalMeta').innerHTML = '<span class="tp"></span> ' + ok + ' / ' + cards.length + ' 就绪';
    const rc = $('readyCount');
    if (rc) rc.textContent = ok + ' / ' + cards.length;
    updateSetupHint();
  }

  /* ---------------- 生成选项（那三个药丸按钮） ---------------- */
  // 之前它们是纯装饰：没有 id、没有事件，点了什么都不发生。
  function chipOn(id) {
    const el = $(id);
    return !!(el && el.classList.contains('on'));
  }
  ['chipVoice', 'chipConsist', 'chipReview', 'chipFinal'].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('click', () => {
      el.classList.toggle('on');
      const on = el.classList.contains('on');
      addLog((on ? '✔ 已开启：' : '· 已关闭：') + el.textContent.trim());
      if (id === 'chipReview' && on) toast('生成完会逐个节点停下来给你过审');
      if (id === 'chipFinal' && on) toast('生成完会多跑一个质检 Agent 复查成品');
    });
  });

  /* ---------------- 窗口按钮（桌面窗口下才有效） ---------------- */
  // 页面自己画的 − / × 之前也是装饰；pywebview 下走 window.pywebview.api 控制真窗口。
  function winApi() {
    return (window.pywebview && window.pywebview.api) || null;
  }
  $('btnWinMin').addEventListener('click', () => {
    const a = winApi();
    if (a && a.minimize) { a.minimize(); return; }
    toast('浏览器里没法最小化，用桌面启动器打开才有');
  });
  $('btnWinClose').addEventListener('click', () => {
    const a = winApi();
    if (a && a.close) { a.close(); return; }
    toast('浏览器里没法关窗口，用桌面启动器打开才有');
  });

  /* ---------------- 标题栏拖动 ---------------- */
  // 无边框窗口：CSS 的 -webkit-app-region 在 WebView2 里不生效，
  // 系统那套 WM_NCLBUTTONDOWN 拖动循环也不认注入的鼠标；
  // 所以自己算增量，逐帧让窗口跟着走（move_by → SetWindowPos）。
  (function bindTitlebarDrag() {
    const bar = document.querySelector('.titlebar');
    if (!bar) return;
    let drag = null;          // { mx, my, wx, wy } 按下时的鼠标(逻辑)与窗口(物理)原点
    let raf = 0;              // requestAnimationFrame 句柄：合并同一帧内的多次 mousemove
    let pending = null;       // 该帧要挪到的绝对物理坐标

    const isInteractive = (el) => !!el.closest(
      'button, input, select, textarea, a, .pillnav, .wincontrols, .mode-switch, .perf-toggle');

    const flush = () => {
      raf = 0;
      if (!drag || !pending) return;
      const a = winApi();
      if (a && a.set_window_pos) a.set_window_pos(pending.x, pending.y);
      else if (a && a.move_by) a.move_by(pending.x - drag.wx, pending.y - drag.wy);
    };

    bar.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      const a = winApi();
      if (!a) return;                          // 浏览器里没有窗口，直接放过
      if (isInteractive(e.target)) return;     // 按钮/输入框上按下不拖窗口
      // 只走「自己算增量 + SetWindowPos」这条路。
      // ✗ native_drag()（WM_NCLBUTTONDOWN）看着更"原生"，但实测系统不认
      //   注入出来的鼠标状态，消息发了窗口纹丝不动，而且它会 return 掉下面这条活路。
      if (!a.move_by && !a.set_window_pos) return;
      e.preventDefault();                      // 别让浏览器起拖图片/选文字手势
      e.stopPropagation();
      const dpr = window.devicePixelRatio || 1;
      // ★ 同步只记鼠标原点，窗口原点异步补 —— 千万不要 await 再赋值 drag：
      //   等待那几十毫秒里鼠标已经在动，位移会被整段丢掉，窗口就"少走一截"。
      drag = { mx: e.screenX, my: e.screenY, wx: null, wy: null, dpr };
      document.body.classList.add('dragging-window');
      if (a.get_window_pos) {
        Promise.resolve().then(() => a.get_window_pos()).then((pos) => {
          if (drag && pos) { drag.wx = pos.x; drag.wy = pos.y; }
        }).catch(() => {});
      } else {
        drag.wx = 0; drag.wy = 0;
      }
    });

    window.addEventListener('mousemove', (e) => {
      if (!drag) return;
      // 窗口原点还没回来就先别动：宁可不跟手，也不要漂。一旦回来，从按下点
      // 算的是绝对位置，前面丢掉的位移会一次补齐。
      if (drag.wx === null) return;
      // 绝对定位：目标 = 按下时的窗口原点 + 鼠标位移。
      // 每帧都用「当前鼠标 - 按下时鼠标」重算，丢帧也不会累积漂移。
      const x = drag.wx + Math.round((e.screenX - drag.mx) * drag.dpr);
      const y = drag.wy + Math.round((e.screenY - drag.my) * drag.dpr);
      pending = { x, y };
      if (!raf) raf = requestAnimationFrame(flush);
    });

    const stop = () => {
      if (!drag) return;
      drag = null;
      pending = null;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      document.body.classList.remove('dragging-window');
    };
    window.addEventListener('mouseup', stop);
    window.addEventListener('blur', stop);
    // 指针跑到窗外（WebView2 会丢 mousemove）时也要收尾
    document.addEventListener('mouseleave', () => { /* 留在窗口内拖动时不触发 */ });
  })();

  /* ---------------- 接口配置：「怎么申请」说明 + 「前往网站」 ---------------- */
  const PROVIDER_GUIDE = {
    qwen: {
      title: '通义千问 · 生图 怎么申请',
      apply: 'https://bailian.console.aliyun.com/?tab=model#/api-key',
      steps: [
        '注册阿里云账号并完成实名认证（个人认证就够）。',
        '打开「百炼」控制台 → 左侧「API-KEY 管理」→ 创建我的 API-KEY。',
        '把生成的 Key 粘到这张卡的「API Key」输入框。',
        '模型选 wanx2.1-t2i-plus（画质好）；想快就选 turbo。',
        '点「测试连接」，显示延迟和余额就通了。',
      ],
      note: '生图按张计费，一张几毛钱；新账号有免费额度，用完要充值。',
    },
    tts: {
      title: '百炼 · 语音合成 怎么申请',
      apply: 'https://help.aliyun.com/zh/model-studio/get-api-key',
      steps: [
        '和生图用的是同一个阿里云账号，同一个 API Key 就能跑。',
        '如果生图那张卡已经填好了，这里直接填同一个 Key。',
        '音色在「接口配置」里选，常用的有 longxiaochun（女）、longlaotie（男）。',
        '点「测试连接」，显示采样率就通了。',
      ],
      note: '按语音条数和字数计费；自动配音会为每句台词合成一条。',
    },
    llm: {
      title: '大语言模型 · 剧本 怎么申请',
      apply: 'https://platform.deepseek.com/api_keys',
      steps: [
        '打开 DeepSeek 开放平台，注册账号。',
        '左侧「API keys」→ 创建 API key，复制那串 sk- 开头的。',
        'Base URL 保持默认 https://api.deepseek.com/v1 不用改。',
        '模型选 deepseek-chat；温度 0.85 偏创作，写剧本合适。',
        '点「测试连接」，显示上下文长度就通了。',
      ],
      note: '四个 agent（编剧/美术/配音/装配）共用这一把 Key。剧本阶段调用最多。',
    },
  };

  function showProviderGuide(kind) {
    const g = PROVIDER_GUIDE[kind];
    if (!g) return;
    const old = document.querySelector('.guide-mask');
    if (old) old.remove();
    const mask = document.createElement('div');
    mask.className = 'guide-mask';
    mask.innerHTML =
      '<div class="guide-box">' +
        '<div class="guide-head"><b>' + esc(g.title) + '</b>' +
          '<button class="guide-x" aria-label="关闭">×</button></div>' +
        '<ol class="guide-steps">' + g.steps.map(function (s) { return '<li>' + esc(s) + '</li>'; }).join('') + '</ol>' +
        '<p class="guide-note">' + esc(g.note) + '</p>' +
        '<div class="guide-foot">' +
          '<button class="btn sm link" data-open-url="' + esc(g.apply) + '">打开申请页 ↗</button>' +
          '<button class="btn sm guide-ok">知道了</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(mask);
    const close = function () { mask.remove(); };
    mask.addEventListener('click', function (e) { if (e.target === mask) close(); });
    mask.querySelector('.guide-x').addEventListener('click', close);
    mask.querySelector('.guide-ok').addEventListener('click', close);
  }

  // 委托绑定：「怎么申请」弹说明，「前往网站」走系统默认浏览器
  document.addEventListener('click', async function (e) {
    const helpBtn = e.target.closest('[data-help]');
    if (helpBtn) { showProviderGuide(helpBtn.dataset.help); return; }
    const openBtn = e.target.closest('[data-open-url]');
    if (!openBtn) return;
    const url = openBtn.dataset.openUrl;
    const host = url.replace(/^https?:\/\//, '').split('/')[0];
    // ★ 优先让桌面壳（pywebview 主进程）去开：它是有前台窗口的交互进程。
    //   后端 uvicorn 是 CREATE_NO_WINDOW 起的后台进程，在里面拉浏览器会被拦
    //   （实测七种方式全部「返回成功但浏览器不出现」）。
    const a = winApi();
    if (a && a.open_url) {
      try {
        const ok = await a.open_url(url);
        if (ok) { toast('已在浏览器打开 ' + host); return; }
        addLog('⚠ 桌面壳打开外链返回失败，改用后端试试');
      } catch (e3) {
        addLog('⚠ 桌面壳打开外链异常：' + (e3 && e3.message ? e3.message : e3));
      }
    }
    try {
      // pywebview 里 window.open 会被 webview 自己拦，所以走后端调系统浏览器
      const r = await fetch(GalAPI.base + '/api/open-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url }),
      });
      const b = await r.json().catch(function () { return {}; });
      if (!r.ok) throw new Error((b.error && b.error.message) || ('HTTP ' + r.status));
      toast('已在浏览器打开 ' + host + '（若显示的是别的页面，按 Ctrl+Tab 切到新标签）');
    } catch (err) {
      let popped = false;
      try { popped = !!window.open(url, '_blank'); } catch (e2) { popped = false; }
      if (popped) {
        toast('已在浏览器打开 ' + host);
      } else {
        toast('打不开浏览器：' + ((err && err.message) || err) + '，链接：' + url);
        addLog('⚠ 打开外链失败：' + ((err && err.message) || err) + ' → ' + url);
      }
    }
  });

  /* ---------------- 底部提示：按实际状态算，不写死数字 ---------------- */
  function updateSetupHint() {
    const el = $('setupHint');
    if (!el) return;
    const cards = Array.from(document.querySelectorAll('.api-card'));
    if (!cards.length) { el.textContent = '正在读取接口配置…'; return; }
    const okCards = cards.filter((c) => c.querySelector('.dot.ok'));
    const isCloud = (c) => /cloud/.test(c.className) || !!c.closest('.setup-col.cloud');
    const cloud = okCards.filter(isCloud).length;
    const local = okCards.length - cloud;
    const dead = cards.filter((c) => !c.querySelector('.dot.ok'))
      .map((c) => (c.querySelector('.t') || {}).textContent || '')
      .filter(Boolean);
    if (!okCards.length) {
      el.textContent = '还没有可用接口：至少填一个（云端或本地），点「测试连接」通过后才能生成。';
      return;
    }
    let text = '云端 ' + cloud + ' 个、本地 ' + local + ' 个可用（共 ' + cards.length + ' 个）。';
    if (dead.length) text += ' 没就绪：' + dead.slice(0, 3).join('、') + '。';
    el.textContent = text;
  }

  /* ---------------- 生成流程：走 GalAPI，mock/real 同一套事件 ---------------- */
  let generating = false;
  let projectId = null;
  let currentJobId = null;
  let finishingPromise = null;   // 收尾（含从后端拉图）是异步的，generate 要等它
  // 质检不是每次都有（用户开了「收尾验证」才跑），权重给小份额；
  // 没开时 genProgress.stages.review 直接置 1，进度条不会被它拖住。
  const AGENT_WEIGHTS = { writer: 0.58, art: 0.20, voice: 0.14, build: 0.05, review: 0.03 };
  const AGENT_NAMES = { writer: '编剧', art: '美术', voice: '配音', build: '装配', review: '质检' };
  // 收尾验证的报告（完成面板里回看用）
  let lastReviewReport = null;
  // 逐节点审核：待审队列 + 当前审到第几个
  let pendingReview = [];
  let reviewIndex = 0;
  const genProgress = { stages: {}, written: new Set(), target: 0, total: 0, chapter: '等待任务启动' };

  function resetGenerationProgress() {
    genProgress.stages = {};
    genProgress.written = new Set();
    genProgress.target = +$('rngNodes').value;
    genProgress.total = 0;
    genProgress.chapter = '解析需求，规划章节';
    $('generationProgress').hidden = false;
    updateGenerationProgress();
  }

  function updateGenerationProgress() {
    let total = 0;
    Object.keys(AGENT_WEIGHTS).forEach((k) => { total += AGENT_WEIGHTS[k] * (genProgress.stages[k] || 0); });
    genProgress.total = Math.max(genProgress.total, Math.min(1, total));
    const percent = Math.round(genProgress.total * 100);
    $('gpPercent').textContent = percent + '%';
    $('gpBar').style.width = percent + '%';
    $('gpChapter').textContent = genProgress.chapter;
    $('gpNodes').textContent = '已写 ' + genProgress.written.size + ' / 目标 ' + genProgress.target + ' 节点';
    window.__galGenerationProgress = { percent: percent, chapter: genProgress.chapter, written: genProgress.written.size, target: genProgress.target, jobId: currentJobId };
  }

  function chapterFromEvent(ev) {
    if (ev.chapter_title) return ev.chapter_title;
    if (ev.chapter_id && Array.isArray(graphChapters)) {
      const c = graphChapters.filter((x) => x.id === ev.chapter_id)[0];
      if (c) return c.title || c.id;
    }
    const m = String(ev.msg || '').match(/(?:第\s*)?([0-9一二三四五六七八九十]+)\s*章[：:\s]*([^，。·…]*)/);
    return m ? ('第 ' + m[1] + ' 章' + (m[2] ? ' · ' + m[2] : '')) : genProgress.chapter;
  }

  $('btnCancelGeneration').addEventListener('click', async () => {
    if (!generating) return;
    $('btnCancelGeneration').disabled = true;
    if (currentJobId) await GalAPI.cancel(currentJobId);
    addLog('■ 用户取消生成');
    finishingPromise = finishGeneration(false);
    $('generationProgress').hidden = true;
    $('btnCancelGeneration').disabled = false;
  });

  function projectName() {
    // 项目名那一行按主人要求删掉了，名字改从记住的记录里取；没有就临时生成一个
    try {
      const saved = localStorage.getItem('gal_project_name');
      if (saved && saved.trim()) return saved.trim();
    } catch (e) {}
    const d = new Date();
    const p = function (n) { return String(n).padStart(2, '0'); };
    return '未命名 ' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' +
      p(d.getHours()) + ':' + p(d.getMinutes());
  }

  async function ensureProjectId() {
    if (projectId) return projectId;
    if (GalAPI.mode === 'mock') { projectId = 'demo'; return projectId; }
    // ★ 已经有项目就别再偷偷建一个：
    //   以前这里直接拿时间戳新建，导致「建完项目名字丢了」后面又冒出
    //   一个「未命名 11-08 19:46」的重复项目（实测踩到）。
    //   改成先接管最近的那个项目。
    try {
      const list = await GalAPI.listProjects();
      if (Array.isArray(list) && list.length) {
        const first = list[0];
        rememberProject(first.id, first.name || projectName());
        addLog('接管已有项目：' + (first.name || first.id));
        return projectId;
      }
    } catch (e) {}
    const p = await GalAPI.ensureProject(projectName());
    rememberProject(p.id, p.name);
    return projectId;
  }

  /* ---------------- 项目记忆：关掉软件再打开能接着编辑 ---------------- */
  const LS_PROJECT = 'gal_project_id';
  const LS_REQ = 'gal_req_text';

  function rememberProject(id, name) {
    projectId = id;
    window.__galProjectId = id || null;   // 供调试/自动化观察当前绑定的是哪个项目
    try {
      if (id) localStorage.setItem(LS_PROJECT, id);
      if (name) localStorage.setItem('gal_project_name', name);
    } catch (e) {}
    // 左栏的项目名标题（原来是可编辑输入框，已按要求改成只读显示）
    const titleEl = document.getElementById('projTitle');
    if (titleEl && name) titleEl.textContent = name;
  }

  // 启动时先把记住的项目名显示出来（真项目恢复失败也不至于显示成空的）
  (function initProjTitle() {
    const el = document.getElementById('projTitle');
    if (!el) return;
    try {
      const saved = localStorage.getItem('gal_project_name');
      if (saved && saved.trim()) el.textContent = saved.trim();
    } catch (e) {}
  })();

  function forgetProject() {
    projectId = null;
    window.__galProjectId = null;
    try { localStorage.removeItem(LS_PROJECT); } catch (e) {}
  }

  /** 启动时接着上次的项目干；后端没起来/项目没了就老实说一句，不假装成功 */
  async function restoreLastProject() {
    if (GalAPI.mode !== 'real') return false;
    let id = null;
    try { id = localStorage.getItem(LS_PROJECT); } catch (e) {}
    if (!id) return false;
    const h = await GalAPI.detect();
    updateModeUI();
    if (!h) {
      addLog('上次的项目「' + id.slice(0, 10) + '…」还在，但后端没起来（' +
        (GalAPI.lastError || GalAPI.base) + '）');
      return false;
    }
    try {
      const g = await GalAPI.getGraph(id);
      if (!g || !Array.isArray(g.nodes) || !g.nodes.length) {
        addLog('上次的项目没有节点，已忽略');
        forgetProject();
        return false;
      }
      projectId = id;
      window.__galProjectId = id;      // 调试观测字段，恢复路径也要同步，否则自动化会误判成"没绑定项目"
      const c = applyGraph(g);
      const nm = (g.project && g.project.name) || '';
      if (nm) {
        const el = document.querySelector('.proj-name input');
        if (el) el.value = nm;
      }
      addLog('✔ 已恢复上次的项目：' + (nm || id) + '（' + c.nodes + ' 节点 / ' + c.links + ' 连线）');
      return true;
    } catch (e) {
      addLog('恢复上次项目失败：' + (e.message || e) + '（已清除记录）');
      forgetProject();
      return false;
    }
  }

  /** 开新项目：清掉记录，画布回到空白起点（不删后端旧项目） */
  async function newProject(quiet) {
    // ★ 真在后端建一个项目（带「起点→结局」骨架），而不是只把界面清空。
    //   ✗ 以前只 forgetProject()：projectId 变成 null，后面所有编辑都没地方存，
    //     自动保存直接 return —— 新建出来的东西等于白做的（实测踩到）。
    if (GalAPI.mode === 'real') {
      const d = new Date();
      const p2 = (n) => String(n).padStart(2, '0');
      const nm = '未命名 ' + p2(d.getMonth() + 1) + '-' + p2(d.getDate()) + ' ' +
        p2(d.getHours()) + ':' + p2(d.getMinutes());
      let created;
      try {
        created = await GalAPI.ensureProject(nm);
      } catch (e) {
        toast('新建项目失败：' + ((e && e.message) || e));
        return false;
      }
      let nodeCount = 0;
      try {
        const g = await GalAPI.getGraph(created.id);
        if (g && Array.isArray(g.nodes)) nodeCount = applyGraph(g).nodes;
      } catch (e) {
        // 骨架拉不下来也先绑上项目，至少后面能存
      }
      rememberProject(created.id, nm);
      const el2 = document.querySelector('.proj-name input');
      if (el2) el2.value = nm;
      $('reqText').value = '';
      updateCount();
      try { localStorage.removeItem(LS_REQ); } catch (e) {}
      setSaveBadge('已自动保存', 'saved');     // 刚建完 = 后端已有这份数据
      addLog('已新建项目「' + nm + '」：骨架 ' + nodeCount + ' 节点，之后的改动会自动保存');
      if (!quiet) {
        toast('已新建「' + nm + '」· ' + nodeCount + ' 节点，改动自动保存');
        go('editor');
      }
      return true;
    }

    // 模拟模式：没有后端可存，只清界面
    forgetProject();
    graphProject = null;
    graphCharacters = null;
    rawById = {};
    const el = document.querySelector('.proj-name input');
    if (el) el.value = '未命名项目';
    $('reqText').value = '';
    updateCount();
    try { localStorage.removeItem(LS_REQ); } catch (e) {}
    addLog('已新建空项目（模拟模式下不落盘）');
    if (!quiet) toast('模拟模式：新建只在内存里，切到真后端才会真的建');
    return true;
  }

  function setAgent(k, state, label, progress) {
    const el = document.querySelector('.agent[data-k="' + k + '"]');
    if (!el) return;
    el.classList.remove('run', 'done', 'failed');
    if (state && state !== 'idle') el.classList.add(state);
    if (label) el.querySelector('.st').textContent = label;
    if (typeof progress === 'number') {
      el.querySelector('.bar i').style.width = Math.round(Math.max(0, Math.min(1, progress)) * 100) + '%';
    }
  }

  /** 后端 SSE 的 node 事件 → 局部刷新画布 */
  function applyNodePatch(id, patch) {
    const n = nodes.find((x) => x.id === id);
    if (!n) return;
    if (Array.isArray(patch.lines)) {
      n.text = patch.lines.map((l) => l.text).join(' ');
      n.who = (patch.lines[0] && patch.lines[0].who) || n.who;
      n.voice = patch.lines.reduce((s, l) => s + (l.dur || 0), 0);
    }
    if (patch.bg && patch.bg.asset) n.bgAsset = patch.bg.asset;
    if (patch.chars && patch.chars[0] && patch.chars[0].pose) n.charAsset = patch.chars[0].pose;
    if (Array.isArray(patch.choices)) n.choices = patch.choices.map((c) => ({ label: c.label, to: c.to }));
    if (patch.status) n.status = patch.status;

    const el = nodeEl(id);
    if (el) { el.innerHTML = nodeHTML(n); bindPorts(el, n); }
    if (window.GalEditor && GalEditor.updateNode) GalEditor.updateNode(id, n);
    drawWires();
  }

  function onGenEvent(type, ev) {
    if (type === 'jobstart') { currentJobId = ev.job_id; updateGenerationProgress(); return; }
    if (!generating && type !== 'done' && type !== 'error') return;
    if (type === 'agent') {
      setAgent(ev.agent, ev.state, ev.msg || ev.state, ev.progress);
      if (AGENT_WEIGHTS[ev.agent] !== undefined) {
        genProgress.stages[ev.agent] = ev.state === 'done' ? 1 : Math.max(genProgress.stages[ev.agent] || 0, Math.max(0, Math.min(1, ev.progress || 0)));
        $('gpAgent').textContent = AGENT_NAMES[ev.agent] + ' Agent · ' + (ev.state === 'done' ? '已完成' : ev.state === 'failed' ? '失败' : '工作中');
        genProgress.chapter = chapterFromEvent(ev);
        updateGenerationProgress();
      }
      if (ev.msg) addLog('▸ ' + ev.agent + '：' + ev.msg);
      return;
    }
    if (type === 'job') {
      if (ev.state === 'failed' || ev.state === 'cancelled') {
        addLog('✖ ' + (ev.error || '任务已中断'));
        finishingPromise = finishGeneration(false);
      }
      return;
    }
    if (type === 'log') {
      addLog((ev.level === 'warn' ? '⚠ ' : '· ') + ev.text);
      return;
    }
    if (type === 'node') {
      applyNodePatch(ev.node_id, ev.patch || {});
      if (!ev.patch || ev.patch.status !== 'pending') genProgress.written.add(ev.node_id);
      updateGenerationProgress();
      return;
    }
    if (type === 'review') {
      // 收尾验证报告：判定 + 分数 + 前若干条问题，全部进日志（报告本身存起来，可回看）
      const r = ev.report || {};
      lastReviewReport = r;
      const mark = r.verdict === 'pass' ? '✔' : r.verdict === 'warn' ? '⚠' : '✖';
      const label = r.verdict === 'pass' ? '通过' : r.verdict === 'warn' ? '将就可用' : '必须修';
      addLog(mark + ' 收尾验证：' + label + ' · ' + (r.score != null ? r.score + ' 分' : ''));
      if (r.summary) addLog('  ' + r.summary);
      const items = r.items || [];
      items.slice(0, 8).forEach((it) => {
        addLog((it.level === 'error' ? '  ✖ ' : '  ⚠ ') + (it.code || '') + '：' + (it.message || ''));
      });
      if (items.length > 8) addLog('  …另有 ' + (items.length - 8) + ' 条，点「质检报告」查看');
      setAgent('review', 'done', label, 1);
      return;
    }
    if (type === 'review_ready') {
      // 逐节点审核：拿到待审队列，逐个弹给用户过
      pendingReview = (ev.pending || []).slice();
      reviewIndex = 0;
      addLog('逐节点审核：待审 ' + pendingReview.length + ' / 共 ' + (ev.total || 0) + ' 个节点');
      openReviewPanel();
      return;
    }
    if (type === 'done') {
      finishingPromise = finishGeneration(true, ev.stats);
      return;
    }
    if (type === 'error') {
      addLog('✖ ' + (ev.code || 'ERROR') + '：' + (ev.message || '未知错误'));
      toast('生成失败：' + (ev.message || ev.code));
      finishingPromise = finishGeneration(false);
    }
  }

  /* ---------------- 逐节点审核面板 ---------------- */
  // 生成完（用户开了「逐节点审核」）后，后端发 review_ready 带待审队列，
  // 这里逐条弹给用户：通过 / 驳回（可写理由）/ 跳过。
  // 判定写回 node.review，重跑生成不会覆盖已审过的节点。
  let reviewMask = null;

  function openReviewPanel() {
    if (!pendingReview.length) { toast('没有待审的节点'); return; }
    if (!reviewMask) {
      reviewMask = document.createElement('div');
      reviewMask.className = 'review-mask';
      reviewMask.innerHTML =
        '<div class="review-box">' +
          '<div class="review-head"><b>逐节点审核</b><span class="review-count"></span>' +
            '<button class="review-x" title="先不审了，随时可以从左侧再打开">×</button></div>' +
          '<div class="review-body"></div>' +
          '<div class="review-foot">' +
            '<input class="review-note" maxlength="500" placeholder="驳回理由（可留空，会记在这个节点上）">' +
            '<div class="review-acts">' +
              '<button class="rb all" title="剩下的节点一次全标记为通过（已审过的不会被覆盖）">其余全部通过</button>' +
              '<button class="rb pass">通过</button>' +
              '<button class="rb fail">驳回</button>' +
              '<button class="rb skip">跳过</button>' +
            '</div>' +
          '</div>' +
        '</div>';
      document.body.appendChild(reviewMask);
      reviewMask.querySelector('.review-x').addEventListener('click', closeReviewPanel);
      reviewMask.querySelector('.rb.all').addEventListener('click', submitReviewAll);
      reviewMask.querySelector('.rb.pass').addEventListener('click', function () { submitReview('pass'); });
      reviewMask.querySelector('.rb.fail').addEventListener('click', function () { submitReview('fail'); });
      reviewMask.querySelector('.rb.skip').addEventListener('click', function () {
        reviewIndex += 1;
        renderReviewStep();
      });
    }
    reviewMask.hidden = false;
    renderReviewStep();
  }

  function closeReviewPanel() {
    if (reviewMask) reviewMask.hidden = true;
    const left = Math.max(0, pendingReview.length - reviewIndex);
    if (left) addLog('逐节点审核：先关掉了，还有 ' + left + ' 个没审');
  }

  function renderReviewStep() {
    if (!reviewMask) return;
    const body = reviewMask.querySelector('.review-body');
    const count = reviewMask.querySelector('.review-count');
    body.textContent = '';
    while (body.firstChild) body.removeChild(body.firstChild);

    if (reviewIndex >= pendingReview.length) {
      count.textContent = '';
      const done = document.createElement('div');
      done.className = 'review-done';
      done.textContent = '这些节点都审完了';
      body.appendChild(done);
      addLog('逐节点审核：本轮全部审完，共 ' + pendingReview.length + ' 个');
      return;
    }

    const n = pendingReview[reviewIndex];
    const typeLabel = ({ start: '序章', scene: '场景', choice: '选择', ending: '结局' })[n.type] || n.type;
    count.textContent = (reviewIndex + 1) + ' / ' + pendingReview.length;

    const t = document.createElement('div');
    t.className = 'review-type';
    t.textContent = typeLabel;
    const h = document.createElement('div');
    h.className = 'review-title';
    h.textContent = n.title || '（无标题）';
    const b = document.createElement('div');
    b.className = 'review-text';
    b.textContent = n.text || '（没有台词）';
    body.appendChild(t); body.appendChild(h); body.appendChild(b);
  }

  async function submitReviewAll() {
    const left = pendingReview.length - reviewIndex;
    if (!left) { toast('剩下的都审完了'); return; }
    const r = await GalAPI.reviewBatch(projectId, 'pass', '批量通过');
    if (!r || !r.ok) {
      toast('批量通过失败：' + ((r && r.detail) || '未知错误'));
      return;
    }
    addLog('✔ 其余 ' + left + ' 个节点一次标记通过' +
      (r.updated != null ? '（后端更新 ' + r.updated + ' 个）' : ''));
    reviewIndex = pendingReview.length;
    renderReviewStep();
  }

  async function submitReview(status) {
    const n = pendingReview[reviewIndex];
    if (!n) return;
    const noteEl = reviewMask.querySelector('.review-note');
    const note = (noteEl && noteEl.value) || '';
    const r = await GalAPI.reviewNode(projectId, n.id, status, note);
    if (!r || !r.ok) {
      toast('保存审核结果失败：' + ((r && r.detail) || '未知错误'));
      return;
    }
    addLog((status === 'pass' ? '✔ 通过：' : '✖ 驳回：') + (n.title || n.id) +
      (note ? '（' + note.slice(0, 30) + '）' : ''));
    if (noteEl) noteEl.value = '';
    reviewIndex += 1;
    renderReviewStep();
  }

  async function finishGeneration(ok, stats) {
    generating = false;
    currentJobId = null;
    if (ok) {
      genProgress.total = 1;
      // 没开「收尾验证」时不会有 review 事件，这里补满，否则进度条永久停在 97%
      genProgress.stages.review = 1;
      genProgress.chapter = '全部章节生成完成';
      $('gpAgent').textContent = '生成完成';
      updateGenerationProgress();
      setTimeout(() => { if (!generating) $('generationProgress').hidden = true; }, 1800);
    }
    const btn = $('btnGenerate');
    btn.classList.remove('running');
    $('genLabel').textContent = ok ? '重新生成剧情' : '重试生成';
    $('agentSummary').textContent = ok ? '已完成' : '已中断';
    // 收尾信号：只有「图已经拉回来、可以继续操作」才算真完成。
    // 按钮退出运行态发生在拉图之前，所以不能拿它当完成判据（自动化会误判）。
    const markDone = () => {
      window.__galLastGen = {
        ok: ok, at: Date.now(), stats: stats || null,
        nodes: nodes.length, links: links.length, mode: GalAPI.mode
      };
    };

    if (!ok) {
      ['writer', 'art', 'voice', 'build'].forEach((k) => {
        const el = document.querySelector('.agent[data-k="' + k + '"].run');
        if (el) setAgent(k, 'failed', '中断');
      });
      markDone();
      return;
    }

    if (GalAPI.mode === 'real') {
      await pullGraphFromBackend();
    } else {
      nodes.forEach((n) => {
        if (n.type !== 'ending' && !(n.voice > 0)) n.voice = +(3.2 + Math.random() * 2.6).toFixed(1);
      });
      render();
    }

    if (stats) {
      addLog('✔ ' + stats.nodes + ' 节点 / ' + stats.edges + ' 连线 / ' + stats.images + ' 张图 / ' +
        stats.voices + ' 条语音 / ' + Math.round(stats.elapsed_s || 0) + 's');
    } else {
      addLog('✔ 生成完成');
    }
    toast('生成完成，可以试玩了');
    markDone();
  }

  /** 真后端：把 graph.json 拉回来替换画布 */
  /**
   * 组装要 PUT 回后端的图。
   * ★ 不能直接把前端 nodes 丢过去：后端一个节点是 lines[] 多条台词，
   *   前端只显示合并后的一句。所以以 _raw 为底，只覆盖前端真正改过的字段，
   *   否则「保存一次 = 把多说压成一句」。
   */
  /* 契约（app/contract.py，extra='forbid'）要求每个节点这些字段一个不能少：
     bg{asset,prompt,seed,provider} / chars / lines[>=1]{who,text[>=1],voice,dur>0}
     / pos / gen{model,at,cost}。少一个整份 PUT 就被 400 拒掉。
     从后端拉来的节点用 _raw 打底自然齐全；本地新建或改过的必须在这里补齐。 */
  function nodeSkeleton(n) {
    return {
      id: n.id,
      type: n.type || 'scene',
      title: n.title || n.id,
      status: 'done',
      bg: {
        // 跟后端 pipeline 的命名一致；写 assets/bg/mock.png 会指向一个真模式不存在的文件
        asset: n.bgAsset || ('assets/bg/' + n.id + '.png'),
        prompt: '',
        seed: 0,
        provider: 'manual'
      },
      chars: [],
      lines: [{
        who: n.who || '',
        text: n.text || '…',
        voice: null,
        dur: (typeof n.voice === 'number' && n.voice > 0) ? n.voice : 1
      }],
      pos: { x: Math.round(n.x || 0), y: Math.round(n.y || 0) },
      gen: { model: 'manual', at: new Date().toISOString(), cost: 0 }
    };
  }

  function projectMeta() {
    const p = graphProject || {};
    return {
      id: p.id || projectId || 'local',
      name: p.name || projectName(),
      resolution: p.resolution || [1920, 1080],
      created_at: p.created_at || new Date().toISOString(),
      // 导出成品的展示信息（和剧情无关）：游戏名 + 图标（data URL）
      game_title: p.game_title || '',
      icon: p.icon || ''
    };
  }

  /* ---------------- 成品设置：导出用的游戏名 + 图标 ---------------- */
  // 存进 graph.project（game_title / icon），导出时由后端取用。
  // 图标直接存 data URL：不用单独管理上传文件的生命周期，导出时解码写进成品。
  let productMask = null;
  let productIcon = '';

  function openProductPanel() {
    if (!productMask) {
      productMask = document.createElement('div');
      productMask.className = 'guide-mask';
      productMask.innerHTML =
        '<div class="guide-box product-box">' +
          '<div class="guide-head"><b>成品设置</b><button class="guide-x" aria-label="关闭">×</button></div>' +
          '<div class="product-body">' +
            '<div class="product-row"><span>游戏名称</span>' +
              '<input class="product-title" maxlength="60" placeholder="导出后显示的名字；留空就用项目名"></div>' +
            '<div class="product-row"><span>游戏图标</span>' +
              '<div class="product-icon-box">' +
                '<div class="product-icon" id="pIcon">没选</div>' +
                '<button class="btn sm" id="pPick">选图片</button>' +
                '<button class="btn sm ghost" id="pClear">清除</button>' +
              '</div></div>' +
            '<p class="product-tip">建议 256×256 的 PNG / ICO，不超过 256KB。' +
              '导出 Ren\'Py 和网页版时会被用上。</p>' +
            '<input type="file" id="pFile" accept="image/png,image/jpeg,image/x-icon" hidden>' +
          '</div>' +
          '<div class="guide-foot"><button class="btn sm" id="pCancel">取消</button>' +
            '<button class="btn sm primary" id="pSave">保存</button></div>' +
        '</div>';
      document.body.appendChild(productMask);
      const close = function () { productMask.hidden = true; };
      productMask.querySelector('.guide-x').addEventListener('click', close);
      productMask.querySelector('#pCancel').addEventListener('click', close);
      productMask.addEventListener('click', function (e) { if (e.target === productMask) close(); });
      productMask.querySelector('#pPick').addEventListener('click', function () {
        productMask.querySelector('#pFile').click();
      });
      productMask.querySelector('#pClear').addEventListener('click', function () {
        productIcon = '';
        renderProductIcon();
      });
      productMask.querySelector('#pFile').addEventListener('change', function (e) {
        const file = e.target.files && e.target.files[0];
        if (!file) return;
        if (file.size > 256 * 1024) {
          toast('图标太大（' + Math.round(file.size / 1024) + 'KB），请压到 256KB 以内');
          return;
        }
        const reader = new FileReader();
        reader.onload = function () {
          productIcon = String(reader.result || '');
          renderProductIcon();
        };
        reader.readAsDataURL(file);
        e.target.value = '';
      });
      productMask.querySelector('#pSave').addEventListener('click', saveProduct);
    }
    const p = graphProject || {};
    productIcon = p.icon || '';
    productMask.querySelector('.product-title').value = p.game_title || '';
    renderProductIcon();
    productMask.hidden = false;
  }

  function renderProductIcon() {
    if (!productMask) return;
    const box = productMask.querySelector('#pIcon');
    if (!box) return;
    if (!productIcon) {
      box.textContent = '没选';
      box.style.backgroundImage = '';
      return;
    }
    box.textContent = '';
    box.style.backgroundImage = 'url("' + productIcon + '")';
  }

  async function saveProduct() {
    const title = (productMask.querySelector('.product-title').value || '').trim();
    graphProject = Object.assign({}, graphProject || {}, { game_title: title, icon: productIcon });
    productMask.hidden = true;
    await saveGraphToBackend();
    toast(title ? ('成品设置已保存：' + title) : '成品设置已保存（游戏名留空，导出时用项目名）');
  }

  /* ---------------- 一键打包（导出页） ---------------- */
  // 和「成品设置」共用同一份数据（graph.project.game_title / icon），
  // 只是这里一次勾多个格式、一起打包、把结果列出来。
  let packIcon = '';

  function renderPackIcon() {
    const box = $('packIcon');
    if (!box) return;
    if (!packIcon) { box.textContent = '没选'; box.style.backgroundImage = ''; return; }
    box.textContent = '';
    box.style.backgroundImage = 'url("' + packIcon + '")';
  }

  function syncPackPanel() {
    if (!$('packTitle')) return;
    const p = graphProject || {};
    packIcon = p.icon || '';
    $('packTitle').value = p.game_title || '';
    renderPackIcon();
  }

  function openLocalPath(path) {
    const a = winApi();
    if (a && a.open_folder) { a.open_folder(path); return; }
    toast('路径：' + path);
  }

  function packRow(label, ok, out, detail) {
    const row = document.createElement('div');
    row.className = 'pack-row ' + (ok ? 'ok' : 'fail');
    const mark = document.createElement('span');
    mark.className = 'pack-mark';
    mark.textContent = ok ? '✔' : '✖';
    const name = document.createElement('b');
    name.textContent = label;
    const path = document.createElement('span');
    path.className = 'pack-path';
    path.textContent = ok ? (out || '（后端没返回路径）') : (detail || '失败');
    row.appendChild(mark); row.appendChild(name); row.appendChild(path);
    if (ok && out) {
      const openBtn = document.createElement('button');
      openBtn.className = 'btn sm ghost';
      openBtn.textContent = '打开位置';
      openBtn.addEventListener('click', function () { openLocalPath(out); });
      row.appendChild(openBtn);
    }
    return row;
  }

  async function runPack() {
    const btn = $('btnPack');
    const state = $('packState');
    const results = $('packResults');
    if (!btn || !state || !results) return;

    const formats = [];
    if ($('packRenpy').checked) formats.push(['renpy', "Ren'Py 工程"]);
    if ($('packWeb').checked) formats.push(['web', '网页播放器']);
    if ($('packExe').checked) formats.push(['unity', 'Windows exe']);
    if (!formats.length) { toast('至少勾一个格式'); return; }
    if (!projectId) {
      // 没绑项目就明确说，别让用户看到一串「项目不存在」还以为打包坏了
      toast('先打开一个项目（顶部「项目」→ 点一个），再回来打包');
      return;
    }

    btn.disabled = true;
    results.hidden = false;
    results.textContent = '';

    // 先把成品信息存进去，后端导出时读的就是这份
    const title = ($('packTitle').value || '').trim();
    graphProject = Object.assign({}, graphProject || {}, { game_title: title, icon: packIcon });
    await saveGraphToBackend();

    for (let i = 0; i < formats.length; i += 1) {
      const fmt = formats[i][0];
      const label = formats[i][1];
      state.textContent = '正在打包 ' + label + '（' + (i + 1) + '/' + formats.length + '）…';
      const r = await GalAPI.exportProject(projectId, fmt);
      if (r && r.ok) {
        results.appendChild(packRow(label, true, r.out));
        if (r.warning) addLog('⚠ ' + label + '：' + r.warning);
      } else {
        results.appendChild(packRow(label, false, '',
          (r && (r.detail || r.message)) || '打包失败'));
      }
    }
    state.textContent = '打包结束';
    btn.disabled = false;
    toast('打包完成，结果在下面');
  }

  /* ---------------- 设置页 ---------------- */
  let settingsCache = null;

  async function loadSettings() {
    if (!$('setProjectsDir')) return;
    try {
      const r = await fetch(GalAPI.base + '/api/settings');
      const s = await r.json();
      settingsCache = s;
      $('setProjectsDir').value = s.projects_dir_override || '';
      $('setProjectsDir').placeholder =
        '当前：' + (s.projects_dir || '') + '（留空 = 程序目录下的 projects）';
      $('setDataDir').textContent = s.data_dir || '—';
      $('setFile').textContent = s.settings_file || '—';
      $('setState').textContent = s.projects_dir_override ? '自定义位置' : '默认位置';
      if ($('setLowPower')) {
        $('setLowPower').checked = document.body.classList.contains('low-power');
      }
    } catch (e) {
      addLog('读设置失败：' + e);
    }
  }

  async function saveSettings() {
    const dir = ($('setProjectsDir').value || '').trim();
    const state = $('setState');
    if (state) state.textContent = '保存中…';
    try {
      const r = await fetch(GalAPI.base + '/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ projects_dir: dir })
      });
      const body = await r.json().catch(function () { return {}; });
      if (!r.ok) {
        const msg = (body.error && body.error.message) || ('HTTP ' + r.status);
        if (state) state.textContent = msg;
        toast(msg);
        return;
      }
      if (state) state.textContent = body.note || '已保存';
      toast(body.restart_required ? '已保存，重启程序后生效' : '已保存');
    } catch (e) {
      if (state) state.textContent = String(e);
      toast('保存失败：' + e);
    }
  }

  function bindSettings() {
    if (!$('setSave')) return;
    $('setSave').addEventListener('click', saveSettings);
    if ($('setResetDir')) {
      $('setResetDir').addEventListener('click', function () {
        $('setProjectsDir').value = '';
        saveSettings();
      });
    }
    if ($('setOpenDir')) {
      $('setOpenDir').addEventListener('click', function () {
        const dir = ($('setProjectsDir').value || '').trim()
          || (settingsCache && settingsCache.projects_dir) || '';
        if (dir) openLocalPath(dir);
      });
    }
    if ($('setOpenData')) {
      $('setOpenData').addEventListener('click', function () {
        const dir = (settingsCache && settingsCache.data_dir) || '';
        if (dir) openLocalPath(dir);
      });
    }
    if ($('setLowPower')) {
      // 低功耗实际开关还是原来那个 #btnPerf，这里跟着它走，别维护两份状态
      $('setLowPower').addEventListener('change', function (e) {
        const want = e.target.checked;
        const now = document.body.classList.contains('low-power');
        if (want !== now && $('btnPerf')) $('btnPerf').click();
      });
    }
  }

  function bindPack() {
    if (!$('btnPack')) return;
    $('packIconPick').addEventListener('click', function () { $('packIconFile').click(); });
    $('packIconClear').addEventListener('click', function () {
      packIcon = '';
      renderPackIcon();
    });
    $('packIconFile').addEventListener('change', function (e) {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      if (file.size > 256 * 1024) {
        toast('图标太大（' + Math.round(file.size / 1024) + 'KB），请压到 256KB 以内');
        return;
      }
      const reader = new FileReader();
      reader.onload = function () {
        packIcon = String(reader.result || '');
        renderPackIcon();
      };
      reader.readAsDataURL(file);
      e.target.value = '';
    });
    $('btnPack').addEventListener('click', runPack);
    if ($('btnOpenPackDir')) {
      $('btnOpenPackDir').addEventListener('click', function () {
        const row = document.querySelector('.pack-row.ok .pack-path');
        if (!row || !row.textContent) { toast('先打包一次，再用每行的「打开位置」'); return; }
        openLocalPath(row.textContent);
      });
    }
  }

  /* ---------------- 界面风格（文字要求 + 参考图 → 设计主界面 / 对话界面） ---------------- */
  // 参考图只拿「色和气质」：前端先缩到最长边 1280 存成 JPEG dataURL 再发给后端，
  // 6 张封顶、单张 6MB 封顶 —— 后端不必再管上传体积和解码。
  const UI_REF_MAX = 6;
  const UI_REF_MAX_BYTES = 6 * 1024 * 1024;
  const UI_REF_LONG_EDGE = 1280;
  let uiRefImages = [];

  function uiDesignState(text) {
    const el = $('uiDesignState');
    if (el) el.textContent = text || '';
  }

  function renderUiRefs() {
    const box = $('uiRefPreview');
    if (!box) return;
    box.textContent = '';
    uiRefImages.forEach(function (src, i) {
      const item = document.createElement('div');
      item.className = 'ui-ref-item';
      const img = document.createElement('img');
      img.src = src;
      img.alt = '参考图 ' + (i + 1);
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'ui-ref-x';
      del.textContent = '✕';
      del.title = '移除这张参考图';
      del.addEventListener('click', function () {
        uiRefImages.splice(i, 1);
        renderUiRefs();
      });
      item.appendChild(img);
      item.appendChild(del);
      box.appendChild(item);
    });
    const drop = $('uiRefDrop');
    if (drop) {
      if (uiRefImages.length) drop.classList.add('has-refs');
      else drop.classList.remove('has-refs');
      drop.title = uiRefImages.length
        ? ('已加 ' + uiRefImages.length + ' 张，最多 ' + UI_REF_MAX + ' 张；点这里还能继续加')
        : ('最多 ' + UI_REF_MAX + ' 张，单张不超过 6MB');
    }
  }

  /**
   * 一张图 → 缩到最长边 1280 → JPEG dataURL。
   * 不用 createImageBitmap：Edge 的版本差异没法保证，new Image() + onload 最稳。
   */
  function compressUiRef(file) {
    return new Promise(function (resolve, reject) {
      const reader = new FileReader();
      reader.onerror = function () { reject(new Error('这张图读不出来')); };
      reader.onload = function () {
        const img = new Image();
        img.onerror = function () { reject(new Error('这张图打不开')); };
        img.onload = function () {
          const w = img.naturalWidth || img.width || 0;
          const h = img.naturalHeight || img.height || 0;
          if (!w || !h) { reject(new Error('这张图尺寸读不出来')); return; }
          const scale = Math.min(1, UI_REF_LONG_EDGE / Math.max(w, h));
          const cw = Math.max(1, Math.round(w * scale));
          const ch = Math.max(1, Math.round(h * scale));
          const cv = document.createElement('canvas');
          cv.width = cw;
          cv.height = ch;
          const ctx = cv.getContext('2d');
          if (!ctx) { reject(new Error('浏览器不给画布，没法压缩')); return; }
          // 透明 PNG 转 JPEG 会变黑，先铺一层白
          ctx.fillStyle = '#ffffff';
          ctx.fillRect(0, 0, cw, ch);
          ctx.drawImage(img, 0, 0, cw, ch);
          resolve(cv.toDataURL('image/jpeg', 0.85));
        };
        img.src = String(reader.result || '');
      };
      reader.readAsDataURL(file);
    });
  }

  async function addUiRefFiles(fileList) {
    const files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    if (uiRefImages.length >= UI_REF_MAX) {
      toast('最多 ' + UI_REF_MAX + ' 张参考图，先删掉几张再加');
      return;
    }
    const room = UI_REF_MAX - uiRefImages.length;
    if (files.length > room) toast('最多 ' + UI_REF_MAX + ' 张参考图，多的没加进来');
    const picked = files.slice(0, room);
    for (let i = 0; i < picked.length; i += 1) {
      const file = picked[i];
      if (!file || !/^image\//.test(String(file.type || ''))) {
        toast('「' + ((file && file.name) || '这张') + '」不是图片，已跳过');
        continue;
      }
      if (file.size > UI_REF_MAX_BYTES) {
        toast('「' + (file.name || '这张') + '」超过 6MB，已跳过');
        continue;
      }
      try {
        uiRefImages.push(await compressUiRef(file));
      } catch (e) {
        toast('「' + (file.name || '这张图') + '」处理失败：' + ((e && e.message) || e));
      }
    }
    renderUiRefs();
  }

  function uiDesignPickTarget() {
    const el = document.querySelector('input[name="uiTarget"]:checked');
    return el ? el.value : 'both';
  }

  /** 成功结果：预设名 + 气质一句话 + 配色块 + 每块界面的 iframe 预览 + 打开 HTML */
  function renderUiDesignResult(res) {
    const box = $('uiDesignResult');
    if (!box) return;
    box.textContent = '';

    const preset = (res && res.preset) || {};
    const head = document.createElement('div');
    head.className = 'ui-design-head';
    const name = document.createElement('b');
    name.textContent = preset.name || '未命名风格';
    head.appendChild(name);
    if (preset.vibe) {
      const vibe = document.createElement('span');
      vibe.textContent = preset.vibe;
      head.appendChild(vibe);
    }
    box.appendChild(head);

    const palette = preset.palette || {};
    const row = document.createElement('div');
    row.className = 'ui-palette';
    ['bg', 'fg', 'accent', 'muted', 'panel'].forEach(function (key) {
      const value = palette[key];
      if (!value) return;
      const chip = document.createElement('i');
      chip.className = 'ui-swatch';
      chip.style.background = value;
      chip.title = key + ' ' + value;
      row.appendChild(chip);
    });
    if (row.children.length) box.appendChild(row);

    const screens = (res && Array.isArray(res.screens)) ? res.screens : [];
    if (!screens.length) {
      const note = document.createElement('p');
      note.className = 'ui-design-note';
      note.textContent = '这次没有生成预览页（模拟模式，或后端没返回 screens）';
      box.appendChild(note);
      return;
    }

    const grid = document.createElement('div');
    grid.className = 'ui-design-screens';
    screens.forEach(function (raw) {
      const item = raw || {};
      const url = item.url || item.src || '';
      const file = item.path || url;
      const label = item.label || item.name || item.screen || item.id || '界面';
      const card = document.createElement('div');
      card.className = 'ui-design-screen';
      const bar = document.createElement('div');
      bar.className = 'ui-design-screen-head';
      const tag = document.createElement('b');
      tag.textContent = label;
      bar.appendChild(tag);
      if (file) {
        const open = document.createElement('button');
        open.type = 'button';
        open.className = 'btn sm ghost';
        open.textContent = '打开 HTML';
        open.addEventListener('click', function () { openLocalPath(file); });
        bar.appendChild(open);
      }
      card.appendChild(bar);
      if (url) {
        const frame = document.createElement('iframe');
        frame.src = url;
        frame.setAttribute('sandbox', 'allow-same-origin');
        frame.setAttribute('loading', 'lazy');
        frame.style.height = '300px';
        card.appendChild(frame);
      }
      grid.appendChild(card);
    });
    box.appendChild(grid);
  }

  /** 失败就写在结果区里，不靠一闪而过的 toast */
  function uiDesignFail(msg) {
    const box = $('uiDesignResult');
    uiDesignState('设计失败');
    if (!box) return;
    box.hidden = false;
    box.textContent = '';
    const p = document.createElement('p');
    p.className = 'ui-design-error';
    p.textContent = msg || '设计失败';
    box.appendChild(p);
  }

  async function runDesignUi() {
    const btn = $('btnDesignUi');
    const box = $('uiDesignResult');
    if (!btn || !box) return;
    if (!projectId) {
      // 和 runPack 一样：没绑项目就先说清楚，别让用户干等
      toast('先打开一个项目（顶部「项目」→ 点一个），再回来设计界面');
      return;
    }
    const payload = {
      requirement: ($('uiRequirement').value || '').trim(),
      references: uiRefImages.slice(),
      target: uiDesignPickTarget()
    };
    btn.disabled = true;
    box.hidden = false;
    box.textContent = '';
    uiDesignState('正在设计界面…');
    let res = null;
    try {
      res = await GalAPI.designUi(projectId, payload);
    } catch (e) {
      res = { ok: false, error: { message: String((e && e.message) || e) } };
    }
    btn.disabled = false;
    if (!res || res.ok === false) {
      uiDesignFail((res && res.error && res.error.message) || (res && res.detail) || '设计失败');
      return;
    }
    uiDesignState('设计完成');
    renderUiDesignResult(res);
  }

  function bindDesignUi() {
    if (!$('btnDesignUi')) return;
    if ($('uiRefPick')) {
      $('uiRefPick').addEventListener('click', function (e) {
        e.preventDefault();
        if ($('uiRefFile')) $('uiRefFile').click();
      });
    }
    if ($('uiRefFile')) {
      $('uiRefFile').addEventListener('change', function (e) {
        addUiRefFiles(e.target.files);
        e.target.value = '';
      });
    }
    const drop = $('uiRefDrop');
    if (drop) {
      // 拖拽：dragover 必须 preventDefault，否则浏览器直接把图打开
      ['dragenter', 'dragover'].forEach(function (type) {
        drop.addEventListener(type, function (e) {
          e.preventDefault();
          drop.classList.add('over');
        });
      });
      ['dragleave', 'dragend'].forEach(function (type) {
        drop.addEventListener(type, function () { drop.classList.remove('over'); });
      });
      drop.addEventListener('drop', function (e) {
        e.preventDefault();
        drop.classList.remove('over');
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
          addUiRefFiles(e.dataTransfer.files);
        }
      });
      drop.addEventListener('click', function (e) {
        // 点空白处也能选图；点「选图片」按钮交给它自己的 handler，别弹两次
        const btn = e.target && e.target.closest ? e.target.closest('button') : null;
        if (btn) return;
        if ($('uiRefFile')) $('uiRefFile').click();
      });
    }
    $('btnDesignUi').addEventListener('click', runDesignUi);
  }

  /** 把一条节点硬修到契约下限，返回修了哪些问题（用来提示用户，不静默改） */
  function conformNode(base, n) {
    const fixed = [];
    if (!base.bg || typeof base.bg !== 'object') { base.bg = nodeSkeleton(n).bg; fixed.push('背景'); }
    if (!base.bg.provider) { base.bg.provider = 'manual'; fixed.push('背景来源'); }
    if (typeof base.bg.asset !== 'string') base.bg.asset = '';
    if (typeof base.bg.prompt !== 'string') base.bg.prompt = '';
    if (typeof base.bg.seed !== 'number') base.bg.seed = 0;
    if (!Array.isArray(base.chars)) base.chars = [];
    if (!base.gen || typeof base.gen !== 'object') {
      base.gen = { model: 'manual', at: new Date().toISOString(), cost: 0 };
      fixed.push('生成信息');
    }
    if (!Array.isArray(base.lines) || !base.lines.length) {
      base.lines = nodeSkeleton(n).lines;
      fixed.push('台词');
    }
    base.lines.forEach((l) => {
      if (typeof l.who !== 'string') l.who = '';
      if (typeof l.text !== 'string' || l.text.length === 0) { l.text = '…'; fixed.push('空台词'); }
      if (typeof l.voice !== 'string' && l.voice !== null) l.voice = null;
      if (typeof l.dur !== 'number' || !(l.dur > 0)) { l.dur = 1; fixed.push('语音时长'); }
    });
    if (!base.pos || typeof base.pos.x !== 'number' || typeof base.pos.y !== 'number') {
      base.pos = { x: Math.round(n.x || 0), y: Math.round(n.y || 0) };
    }
    return fixed;
  }

  function buildGraphPayload() {
    const fixes = [];
    const payload = {
      version: 1,
      project: projectMeta(),
      characters: Array.isArray(graphCharacters) ? graphCharacters : [],
      nodes: nodes.map((n) => {
        const src = rawById[n.id] || n._raw;
        const base = src ? JSON.parse(JSON.stringify(src)) : nodeSkeleton(n);

        base.id = n.id;
        base.type = n.type;
        base.title = n.title;
        base.pos = { x: Math.round(n.x), y: Math.round(n.y) };

        // 后端用 choices 编码【所有出边】——engine/graph.py 的 edges_from_nodes()
        // 只认 n.choices 展开成 (from, port, to)，edges 必须严格等于它。
        // 所以不能只处理 choice 类型：start/scene 也得把出边写回 choices，
        // 否则后端会判「edges 与 choices 不一致」，导出直接 409。
        if (n.type === 'ending') {
          delete base.choices;
        } else {
          const outs = links.filter((l) => l.from === n.id);
          if (n.type === 'choice') {
            // 画布上分支固定 4 个槽，没连线的是 placeholder —— 那不是选项，要丢掉。
            // condition / effects / kind 只在 raw 上，按目标节点对回去，免得保存一次就丢旗标。
            const rawChoices = Array.isArray(base.choices) ? base.choices : [];
            const used = new Set();
            const mapped = (n.choices || []).map((c, k) => {
              if (c.placeholder) return null;
              const lk = outs.filter((l) => (l.port || 0) === k)[0];
              const to = c.to || (lk ? lk.to : '');
              if (!to) return null;
              let ri = rawChoices.findIndex((r, i) => !used.has(i) && r && r.to === to && r.label === c.label);
              if (ri < 0) ri = rawChoices.findIndex((r, i) => !used.has(i) && r && r.to === to);
              if (ri >= 0) used.add(ri);
              const keep = ri >= 0 ? rawChoices[ri] : {};
              const out = { label: c.label, to: to, condition: keep.condition || null };
              if (keep.effects) out.effects = keep.effects;
              if (keep.kind) out.kind = keep.kind;
              return out;
            }).filter(Boolean);
            base.choices = mapped;
          } else {
            const first = outs.filter((l) => (l.port || 0) === 0)[0];
            if (first) {
              const keep = (Array.isArray(base.choices) && base.choices[0]) || {};
              base.choices = [Object.assign({}, keep, { label: keep.label || '继续', to: first.to })];
            } else if (n.type === 'start' || n.type === 'scene') {
              delete base.choices;   // 断头路：没有出边就不写 choices
            }
          }
        }

        // 结局类型以 raw 为准（属性面板直接改的就是 raw）。
        // 早先这里无条件用画布对象的 endKind 覆盖，面板一改就被打回原值。
        if (n.type === 'ending') base.end_kind = base.end_kind || n.endKind || 'true';
        else delete base.end_kind;

        // 分支性质只对 choice 有意义；面板改的 branch_kind 在 raw 里，原样带出。
        if (n.type === 'choice') {
          if (base.branch_kind !== 'key' && base.branch_kind !== 'flavor') base.branch_kind = 'key';
        } else {
          delete base.branch_kind;
        }

        // 台词：单句节点用画布上的那句覆盖；多句节点不能覆盖 ——
        // n.text 是「所有句子的合并串」，写进第 1 句等于把后面的台词吃掉。
        // 面板改台词时已经逐句写进 raw.lines，画布上的合并串只用来显示。
        if (Array.isArray(base.lines) && base.lines.length) {
          if (base.lines.length === 1) {
            base.lines[0].who = n.who || '';
            base.lines[0].text = n.text || '…';
          }
        }

        const f = conformNode(base, n);
        if (f.length) fixes.push(n.id + '(' + f.join('/') + ')');
        return base;
      })
      // 不再发 edges：后端只认 nodes[].choices，edges 由服务端按 choices 重算（D1）。
    };
    if (graphFlags) payload.flags = graphFlags;
    payload._fixes = fixes;   // 供调用方提示，不进请求体
    // 章节是后端新版图契约的一部分，仅在原响应确实提供时原样回传。
    if (Array.isArray(graphChapters)) payload.chapters = graphChapters;
    return payload;
  }

  async function saveGraphToBackend(quiet) {
    syncFromEditor();   // 先把画布上的位置/连线读回来
    // 手动保存时把排队的自动保存取消掉，免得紧接着又存一遍
    if (!quiet && autosaveTimer) { clearTimeout(autosaveTimer); autosaveTimer = null; }
    autosaveDirty = false;
    if (GalAPI.mode === 'mock') {
      if (!quiet) toast('模拟模式下不用保存（切到真后端才有得存）');
      return false;
    }
    if (!projectId) {
      if (!quiet) toast('还没有项目，先点一次「开始生成剧情」');
      return false;
    }
    const btn = $('btnSave');
    if (btn) btn.disabled = true;
    const payload = buildGraphPayload();
    const autoFixed = payload._fixes || [];
    delete payload._fixes;               // 不能发出去：契约 extra='forbid'
    if (autoFixed.length) {
      addLog('⚠ 保存前自动补齐了契约必填字段：' + autoFixed.slice(0, 3).join('、') +
        (autoFixed.length > 3 ? ' 等 ' + autoFixed.length + ' 处' : ''));
    }
    let res;
    try {
      res = await GalAPI.saveGraph(projectId, payload);
    } catch (e) {
      res = { ok: false, detail: String((e && e.message) || e) };
    }
    if (btn) btn.disabled = false;

    if (res && res.ok) {
      const kept = nodes.filter((n) => rawById[n.id]).length;
      addLog('画布已保存：' + nodes.length + ' 节点 / ' + links.length + ' 连线（保留后端元信息 ' +
        kept + '/' + nodes.length + '）');
      if (!quiet) {
        toast('已保存 ' + nodes.length + ' 节点 / ' + links.length + ' 连线');
        setSaveBadge('已保存', 'saved');
      }
      return true;
    }
    const d = (res && res.detail) || '未知错误';
    addLog('保存失败：' + d);
    if (!quiet) toast('保存失败：' + d);
    return false;
  }

  /** 把后端返回的 graph 装进画布（pull 和「恢复上次项目」共用） */
  function applyGraph(g) {
    rawById = {};
    // ★ 角色表必须先就位：下面生成节点时要用它把 who（内部 id）翻成显示名
    graphCharacters = Array.isArray(g.characters) ? g.characters : [];
    nodes = g.nodes.map((raw, i) => {
      rawById[raw.id] = raw;      // ★ 按 id 留底，节点对象重建也不丢
      const lines = raw.lines || [];
      return {
        id: raw.id,
        type: raw.type || 'scene',
        title: raw.title || raw.id,
        idx: raw.idx || String(i + 1).padStart(2, '0'),
        x: raw.pos && typeof raw.pos.x === 'number' ? raw.pos.x : 40 + (i % 5) * 330,
        y: raw.pos && typeof raw.pos.y === 'number' ? raw.pos.y : 320,
        who: (lines[0] && lines[0].who) || '',
        whoName: charName((lines[0] && lines[0].who) || ''),
        text: lines.map((l) => l.text).join(' ') || raw.text || '',
        bg: guessBg(raw),
        bgAsset: (raw.bg && raw.bg.asset) || '',
        charAsset: (raw.chars && raw.chars[0] && raw.chars[0].pose) || '',
        voice: lines.reduce((s, l) => s + (l.dur || 0), 0),
        choices: (raw.choices || []).map((c) => ({ label: c.label, to: c.to, condition: c.condition || null, effects: c.effects || null })),
        endKind: ['true', 'good', 'bad', 'alt'].includes(raw.end_kind) ? raw.end_kind : undefined,
        branchKind: (raw.branch_kind === 'flavor') ? 'flavor' : (raw.type === 'choice' ? 'key' : undefined),
        status: raw.status || 'done',
        _raw: raw
      };
    });
    links = (g.edges || []).map((e) => ({ id: e.id, from: e.from, port: e.port || 0, to: e.to }));
    graphProject = g.project || null;
    graphFlags = (g.flags && typeof g.flags === 'object') ? g.flags : null;
    graphChapters = Array.isArray(g.chapters) ? g.chapters : null;
    // ★ 打开项目默认落到第一章（保留用户当前所在章）：
    //   31 章 510 个节点塞进一个视口要缩到 3%，字全糊 —— 单章视图才是
    //   「一条主干往右、分支上下展开」的可读画面。想看全书点「显示全部」。
    const keepChapter = activeChapterId && graphChapters
      && graphChapters.some((c) => c && c.id === activeChapterId) ? activeChapterId : null;
    activeChapterId = keepChapter || (graphChapters && graphChapters.length ? graphChapters[0].id : null);
    renderChapterTree();

    // choice 节点若后端只给 edges 不给 choices，用 edges 兜底
    nodes.forEach((n) => {
      if (n.type === 'choice' && (!n.choices || !n.choices.length)) {
        n.choices = links.filter((l) => l.from === n.id)
          .sort((a, b) => a.port - b.port)
          .map((l, i) => ({ label: '选项 ' + String.fromCharCode(65 + i), to: l.to }));
      }
    });

    selId = null;
    $('inspector').classList.remove('show');
    render();
    fitView();
    return { nodes: nodes.length, links: links.length };
  }

  async function pullGraphFromBackend() {
    try {
      const g = await GalAPI.getGraph(projectId);
      if (!g || !Array.isArray(g.nodes) || !g.nodes.length) {
        addLog('后端没返回节点图，保持当前画布');
        return;
      }
      const c = applyGraph(g);
      addLog('已载入后端节点图：' + c.nodes + ' 节点 / ' + c.links + ' 连线');
    } catch (e) {
      addLog('✖ 拉取节点图失败：' + (e.message || e));
    }
  }

  function guessBg(raw) {
    const s = ((raw.title || '') + ' ' + ((raw.bg && raw.bg.prompt) || '')).toLowerCase();
    if (/黄昏|dusk|楼梯|走廊/.test(s)) return 'dusk';
    if (/清晨|白|站台|dawn|morning/.test(s)) return 'white';
    return 'rainy';
  }

  /** 角色 id → 显示名（卡片上的立绘角标用中文名，不显示 heroine 这种内部 id） */
  function charName(id) {
    if (!id) return '';
    const c = (Array.isArray(graphCharacters) ? graphCharacters : [])
      .filter((x) => x && x.id === id)[0];
    return (c && (c.name || c.id)) || id;
  }

  async function generate() {
    if (generating) return;
    const req = $('reqText').value.trim();
    if (req.length < 6) { toast('需求太短了，多写一点'); return; }

    generating = true;
    resetGenerationProgress();
    toggleLog(true);            // 开始生成时自动展开日志
    const btn = $('btnGenerate');
    btn.classList.add('running');
    $('genLabel').textContent = '准备中…';
    $('agentSummary').textContent = '运行中';
    ['writer', 'art', 'voice', 'build'].forEach((k) => setAgent(k, 'idle', '待机', 0));

    addLog('任务开始 · 需求 ' + req.length + ' 字 · ' +
      (GalAPI.mode === 'mock' ? '模拟数据' : '真后端 ' + GalAPI.base));

    try {
      await ensureProjectId();
    } catch (e) {
      addLog('✖ 建项目失败：' + (e.message || e));
      finishGeneration(false);
      return;
    }

    const params = {
      node_count: +$('rngNodes').value,
      key_branches: +$('rngKey').value,
      flavor_branches: +$('rngFlavor').value,
      branch_depth: +$('rngBranch').value,
      auto_voice: chipOn('chipVoice'),
      consistency: chipOn('chipConsist'),
      review_per_node: chipOn('chipReview'),
      final_check: chipOn('chipFinal'),
      resolution: [1920, 1080]
    };

    await GalAPI.runGeneration(
      { projectId: projectId, requirement: req, params: params },
      onGenEvent
    );

    // 兜底：流结束但没收到 done/error（比如 SSE 静默断开）
    if (generating) {
      addLog('✖ 进度流结束，未收到完成事件');
      finishingPromise = finishGeneration(false);
    }

    // 等收尾真正做完（含从后端拉图），否则画布会短暂停在旧图
    if (finishingPromise) {
      await finishingPromise;
      finishingPromise = null;
    }
  }
  $('btnGenerate').addEventListener('click', generate);

  $('btnFillDemo').addEventListener('click', () => {
    $('reqText').value = '一个雨夜，天台。主角和从小一起长大的她说好要一起离开这座城。她先到了，手里攥着两张车票。十年之约，两条分支，一个真结局一个坏结局，对话克制、留白，不要独白太多。';
    updateCount();
    toast('已填入示例需求');
  });

  function updateCount() {
    $('reqCount').textContent = $('reqText').value.length + ' 字';
  }
  function persistReq() {
    try { localStorage.setItem(LS_REQ, $('reqText').value); } catch (e) {}
  }
  $('reqText').addEventListener('input', () => { updateCount(); persistReq(); });
  // 先把上次没写完的需求装回来，再统一算字数
  // （顺序不能反：先 updateCount 会把页面默认值写回 localStorage，把存的需求冲掉）
  try {
    const savedReq = localStorage.getItem(LS_REQ);
    if (savedReq) $('reqText').value = savedReq;
  } catch (e) {}
  updateCount();

  /* 节点数和目标时长是同一件事的两种说法 —— 必须联动。
     不联动会出现「90 分钟 / 8 个节点」这种自相矛盾的提交：
     界面上写着 90 分钟，实际生成 8 个节点的图。 */
  const NODES_PER_MINUTE = 300 / 48 * 1.3;          // 每分钟约 8.1 个节点
  // 时长不再单独设滑条：玩家只填节点数，下面用一行小字给「预计游玩多久」。
  const nodesFromMinutes = (m) => Math.round(m * NODES_PER_MINUTE);

  function refreshNodesHint() {
    const n = +$('rngNodes').value;
    $('lblNodes').textContent = n;
    $('lblEstimate').textContent = '约 ' + Math.max(1, Math.round(n / NODES_PER_MINUTE)) + ' 分钟 · 左右即可';
  }

  $('rngNodes').addEventListener('input', refreshNodesHint);
  $('rngKey').addEventListener('input', (e) => { $('lblKey').textContent = e.target.value; });
  $('rngFlavor').addEventListener('input', (e) => { $('lblFlavor').textContent = e.target.value; });
  $('rngBranch').addEventListener('input', (e) => { $('lblBranch').textContent = e.target.value; });
  refreshNodesHint();

  // 可复现的图数据与 SSE 压测入口；正常界面不依赖此接口。
  window.GalFrontendTest = {
    applyGraph: applyGraph,
    // 属性面板回写验收钩子：读某个节点的原始 Node（面板改的就是它）
    inspect: function (id) { return rawOf(id); },
    selected: function () { return selId; },
    setLibrary: function (lib) { libraryOverride = lib; },
    openPicker: openPicker,
    changeType: changeNodeType,
    select: function (id) { selectNode(id); },
    // 验收用：直接绑定一个真项目并拉图（不走 localStorage 那套恢复流程）
    boot: async function (pid) {
      GalAPI.mode = 'real';
      projectId = pid;
      window.__galProjectId = pid;
      const g = await GalAPI.getGraph(pid);
      const c = applyGraph(g);
      render();
      return c;
    },
    characters: function () { return Array.isArray(graphCharacters) ? graphCharacters.slice() : []; },
    currentProject: function () { return projectId; },
    event: onGenEvent,
    beginProgress: function (target) { generating = true; $('rngNodes').value = target || 980; resetGenerationProgress(); },
    progress: function () { return window.__galGenerationProgress; },
    visible: function () { return window.__galVisibleGraph; },
    state: function () { return { nodes: nodes.length, links: links.length, chapters: graphChapters ? graphChapters.length : 0, activeChapterId: activeChapterId }; },
    // 写入门回归钩子：切到某一章的单章视图 / 走一遍真实的「保存」（sync → payload → PUT）
    selectChapter: function (id) { selectChapter(id); },
    chapters: function () { return (graphChapters || []).map((c) => c && c.id); },
    save: function () { return saveGraphToBackend(true); },
    payload: function () { syncFromEditor(); const p = buildGraphPayload(); delete p._fixes; return p; },
    // 试玩播放器验收钩子：跳转指定节点 / 读当前节点与句次
    player: {
      open: function () { openPlayer(); },
      show: function (id) { showNode(id); },
      flags: function () { return Object.assign({}, pl.flags); },
      cur: function () {
        const line = pl.lines[pl.lineIdx] || {};
        return { id: pl.cur, lineIdx: pl.lineIdx, lines: pl.lines.length, lineDone: pl.lineDone, lineText: line.text || '' };
      },
      findByType: function (t) { const n = nodes.find((x) => x.type === t); return n ? n.id : null; },
      findMultiLine: function () {
        const n = nodes.find((x) => x._raw && Array.isArray(x._raw.lines) && x._raw.lines.length >= 2);
        return n ? n.id : null;
      },
      choicesOf: function (id) {
        const n = nodes.find((x) => x.id === id);
        return n ? choiceTargets(n) : [];
      }
    }
  };

  /* ---------------- 试玩播放器 ---------------- */
  const pl = {
    cur: '', typing: null, text: '', step: 'talk', history: [],
    lines: [], lineIdx: 0, lineTimer: null, lineDone: false, audio: null, flags: {}
  };

  /** 旗标回到 graph.flags 声明的默认值（开始试玩 / 从头开始时调用）。 */
  function resetFlags() {
    pl.flags = {};
    Object.keys(graphFlags || {}).forEach((k) => { pl.flags[k] = (graphFlags[k] || {}).default; });
  }

  /** condition 只有两种写法：flag（为真才显示）/ !flag（为假才显示）；空 = 总显示。 */
  function passCondition(c) {
    const cond = c && c.condition;
    if (!cond) return true;
    const neg = cond[0] === '!';
    const value = !!pl.flags[neg ? cond.slice(1) : cond];
    return neg ? !value : value;
  }

  /** effects：布尔直接赋值，整数在原值上累加。 */
  function applyEffects(c) {
    const fx = (c && c.effects) || {};
    Object.keys(fx).forEach((k) => {
      const v = fx[k];
      pl.flags[k] = typeof v === 'number' ? (Number(pl.flags[k]) || 0) + v : v;
    });
  }

  /** 资源 URL：{GalAPI.base}/api/assets/{projectId}/{relPath}，relPath 自带 assets/ 前缀 */
  function playAssetURL(rel) {
    if (!rel || !projectId) return '';
    return GalAPI.assetURL(projectId, rel);
  }

  /** 停掉当前句的语音 / 计时 / 打字机（切节点、关播放器、回退都要走这里，防叠音） */
  function stopPlayback() {
    if (pl.typing) { clearInterval(pl.typing); pl.typing = null; }
    if (pl.lineTimer) { clearTimeout(pl.lineTimer); pl.lineTimer = null; }
    if (pl.audio) {
      try { pl.audio.pause(); } catch (e) {}
      pl.audio.removeAttribute('src');
      try { pl.audio.load(); } catch (e) {}
    }
  }

  /** 试玩从哪个节点进：优先当前章节的第一节点，其次系统起点 */
  function startNodeId() {
    // ★ 以前写死从 start 节点开始 —— 「每点一次试玩都回到第一章」（主人实测）。
    //   现在跟着章节导航走：点开第 5 章，试玩就从第 5 章的第一个节点开始。
    const chapter = visibleChapter();
    const first = chapterNodeIds(chapter)
      .map((id) => nodes.find((x) => x.id === id))
      .find(Boolean);
    if (first) return first.id;
    const s = nodes.find((x) => x.type === 'start') || nodes[0];
    return s ? s.id : 'n1';
  }

  function openPlayer() {
    if (!pl.audio) pl.audio = $('pVoiceAudio');    // 播放器内唯一的 <audio>，复用防叠音
    $('player').classList.add('show');
    pl.history = [];
    pl.cur = '';
    resetFlags();
    showNode(startNodeId());
  }
  $('btnPlay').addEventListener('click', openPlayer);
  $('plClose').addEventListener('click', () => { stopPlayback(); $('player').classList.remove('show'); });
  $('plBack').addEventListener('click', (e) => {
    e.stopPropagation();
    const prev = pl.history.pop();
    if (prev) showNode(prev, true);
    else toast('已经是第一句了');
  });
  $('plRestart').addEventListener('click', (e) => {
    e.stopPropagation();
    pl.history = [];
    pl.cur = '';
    resetFlags();
    showNode(startNodeId());
    toast('从头开始');
  });
  $('player').addEventListener('click', (e) => { if (e.target === $('player')) { stopPlayback(); $('player').classList.remove('show'); } });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { stopPlayback(); $('player').classList.remove('show'); } });

  /** 背景：优先真实素材图，加载失败保留 CSS 兜底（雨/黄昏/白） */
  function setStageBg(n) {
    const bg = $('pBg');
    const cls = n.bg === 'dusk' ? 'dusk' : n.bg === 'white' ? 'white' : 'rainy';
    bg.className = 'bg ' + cls;
    bg.style.backgroundImage = '';
    $('pRain').style.display = cls === 'rainy' ? '' : 'none';
    const url = playAssetURL(n.bgAsset);
    if (!url) return;
    const probe = new Image();
    probe.onload = () => {
      if (pl.cur !== n.id) return;                 // 已经切走，不盖新节点的背景
      bg.style.backgroundImage = 'url("' + url + '")';
      $('pRain').style.display = 'none';           // 真图就位后不再叠 CSS 雨
    };
    probe.onerror = () => {};                      // 失败就保持 CSS 兜底
    probe.src = url;
  }

  /** 取节点原始数据：rawById 是权威底稿，_raw 只是最后兜底（节点重建时可能不在） */
  function nodeRaw(n) {
    if (!n) return null;
    return rawById[n.id] || n._raw || null;
  }

  /** 白底立绘 → 透明。按 url 缓存，每张只处理一次。
   *
   * 为什么要在显示端做：项目里 `assets/ref/*.png` 这类图是白底不透明的
   * （美术 agent 的参考图被节点直接引用了），直接铺到场景上就是一个大白方块。
   * 阈值取得很严（只打 238 以上的纯白），人物身上的浅色高光不会被误伤；
   * 200~238 之间按亮度渐隐，避免边缘出现硬白边。
   */
  const whiteCutCache = new Map();
  function whiteToTransparent(url) {
    if (whiteCutCache.has(url)) return whiteCutCache.get(url);
    const task = new Promise((resolve) => {
      const probe = new Image();
      // ★ 必须声明跨源：素材走 8790 的 /api/assets，页面在 8799 上，
      //   不带这个属性浏览器会把图当普通资源，canvas 一读像素就报
      //   「The canvas has been tainted by cross-origin data」（实测踩到，白底去不掉）。
      //   后端 CORS 是 allow_origins=['*']，带上之后能正常读。
      probe.crossOrigin = 'anonymous';
      probe.onload = () => {
        try {
          const canvas = document.createElement('canvas');
          canvas.width = probe.naturalWidth;
          canvas.height = probe.naturalHeight;
          const ctx = canvas.getContext('2d');
          ctx.drawImage(probe, 0, 0);
          const frame = ctx.getImageData(0, 0, canvas.width, canvas.height);
          const px = frame.data;
          // 已经是透明底就别再处理：canvas 的固定阈值会把边缘越啃越脏，
          // 项目里 assets/ref 下那些图已经离线用 Otsu + unmatte 抠干净了（实测）。
          let translucent = 0;
          for (let i = 3; i < px.length; i += 4) {
            if (px[i] < 250) translucent++;
          }
          if (translucent / (px.length / 4) > 0.01) { resolve(url); return; }
          for (let i = 0; i < px.length; i += 4) {
            const r = px[i], g = px[i + 1], b = px[i + 2];
            if (r > 238 && g > 238 && b > 238) { px[i + 3] = 0; continue; }
            const low = Math.min(r, g, b);
            if (low > 200) px[i + 3] = Math.round(px[i + 3] * (255 - low) / 55);
          }
          ctx.putImageData(frame, 0, 0);
          resolve(canvas.toDataURL('image/png'));
        } catch (e) {
          resolve(url);                        // 处理失败就用原图，别让立绘消失
        }
      };
      probe.onerror = () => resolve(url);
      probe.src = url;
    });
    whiteCutCache.set(url, task);
    return task;
  }

  /** 立绘：最多 3 个，按 chars[i].pos 摆位；全部加载失败时保留剪影兜底 */
  function setStageChars(n) {
    const wrap = $('pChars');
    if (wrap) wrap.innerHTML = '';
    const hideAll = n.type === 'ending';
    $('pChar').style.display = hideAll ? 'none' : '';
    if (hideAll || !wrap) return;
    const src = nodeRaw(n);
    const chars = (src && Array.isArray(src.chars) ? src.chars : [])
      .filter((c) => c && c.pose)
      .slice(0, 3);
    chars.forEach((c, i) => {
      const url = playAssetURL(c.pose);
      if (!url) return;
      const pos = ['left', 'center', 'right'].indexOf(c.pos) >= 0
        ? c.pos
        : (i === 0 ? 'center' : i === 1 ? 'left' : 'right');
      const img = document.createElement('img');
      img.className = 'charImg pos-' + pos;
      img.alt = c.id || '';
      img.onload = () => {
        if (pl.cur === n.id) $('pChar').style.display = 'none';  // 有真立绘就藏剪影
      };
      img.onerror = () => { img.remove(); };
      img.src = url;
      // 白底立绘在显示端口去底：项目里 assets/ref 下的参考图被节点直接引用了，
      // 原样铺上去就是一块白板（实测截到过）。
      whiteToTransparent(url).then((clean) => {
        if (clean !== url && pl.cur === n.id && img.isConnected) img.src = clean;
      });
      wrap.appendChild(img);
    });
  }

  /** 一个节点的台词队列：优先原始 lines（1~2 句），缺失时合成单句 */
  function nodeLines(n) {
    const src = nodeRaw(n);
    const raw = src && Array.isArray(src.lines) ? src.lines : null;
    if (raw && raw.length) {
      return raw.map((l) => ({
        who: l.who || n.who || '',
        text: l.text || '',
        voice: l.voice || '',
        dur: typeof l.dur === 'number' ? l.dur : 0
      }));
    }
    return [{ who: n.who || '', text: n.text || '', voice: '', dur: n.voice || 0 }];
  }

  /** 播当前句：语音 + 打字机 + dur 计时；放完自动进下一句 */
  function startLine() {
    const line = pl.lines[pl.lineIdx];
    if (!line) return;
    pl.lineDone = false;
    $('pWho').textContent = line.who || '旁白';
    $('pWho').style.display = line.who ? '' : 'none';
    $('plVoice').textContent = line.dur > 0 ? line.dur.toFixed(1) + 's' : '无';

    typeText($('pTxt'), line.text || '');

    if (pl.audio) {
      const url = playAssetURL(line.voice);
      if (url) {
        pl.audio.src = url;
        try { pl.audio.currentTime = 0; } catch (e) {}
        const p = pl.audio.play();
        if (p && p.catch) p.catch(() => {});       // 自动播放被拦/文件缺失都不打断流程
      }
    }

    // 音画同步以 dur 为准：不用 audio ended 做唯一依据
    const durMs = line.dur > 0
      ? line.dur * 1000
      : Math.min(8000, (line.text || '').length * 26 + 800);
    pl.lineTimer = setTimeout(() => {
      pl.lineTimer = null;
      pl.lineDone = true;
      advanceLine();                               // 放完自动接下一句
    }, durMs);
  }

  /** 进下一句；已是最后一句就停住等点击 */
  function advanceLine() {
    if (pl.lineIdx < pl.lines.length - 1) {
      pl.lineIdx++;
      if (pl.audio) { try { pl.audio.pause(); } catch (e) {} }
      startLine();
    }
  }

  function showNode(id, fromBack) {
    const n = nodes.find((x) => x.id === id);
    if (!n) return;
    if (!pl.audio) pl.audio = $('pVoiceAudio');    // 兜底绑定，防测试钩子绕过 openPlayer
    if (!fromBack && pl.cur && pl.cur !== id) pl.history.push(pl.cur);
    stopPlayback();                                // 切节点先停上一句，防叠音
    pl.cur = id;
    pl.step = 'talk';
    $('pEnding').style.display = 'none';
    $('pChoices').classList.remove('show');
    $('pDlg').style.display = '';

    setStageBg(n);
    setStageChars(n);

    $('plNode').textContent = n.id;
    $('plIdx').textContent = String(nodes.indexOf(n) + 1);
    $('plTotal').textContent = String(nodes.length);

    pl.lines = nodeLines(n);
    pl.lineIdx = 0;
    startLine();
  }

  function typeText(el, str) {
    if (pl.typing) { clearInterval(pl.typing); pl.typing = null; }
    pl.text = str;
    el.textContent = '';
    let i = 0;
    pl.typing = setInterval(() => {
      i++;
      el.textContent = str.slice(0, i);
      if (i >= str.length) { clearInterval(pl.typing); pl.typing = null; }
    }, 26);
  }

  function finishTyping() {
    if (!pl.typing) return false;
    clearInterval(pl.typing);
    pl.typing = null;
    $('pTxt').textContent = pl.text;
    return true;
  }

  $('stage').addEventListener('click', (e) => {
    if (e.target.closest('.choices')) return;
    if (finishTyping()) return;                    // 第一次点击：补完打字机

    const n = nodes.find((x) => x.id === pl.cur);
    if (!n) return;

    // 当前句还在按 dur 播放：快进掉这句（停语音），进下一句
    if (pl.lineTimer) {
      clearTimeout(pl.lineTimer);
      pl.lineTimer = null;
      pl.lineDone = true;
      if (pl.audio) { try { pl.audio.pause(); } catch (err) {} }
      advanceLine();
      return;
    }
    // 兜底：还有没播的句次就按句推进，两句没放完不许跳到下个节点
    if (pl.lineIdx < pl.lines.length - 1) {
      pl.lineIdx++;
      startLine();
      return;
    }

    if (n.type === 'ending') {
      if (pl.audio) { try { pl.audio.pause(); } catch (err) {} }
      showEnding(n);
      return;
    }

    if (n.type === 'choice' && pl.step === 'talk') {
      pl.step = 'choice';
      showChoices(n);
      return;
    }

    // 出口只读 nodes[].choices（与后端同一份真相），不再读 links。
    const next = realChoices(n)[0];
    if (next) { applyEffects(next); showNode(next.to); return; }
    // 走到死路：如果这条腿是某个选择分出来的，就退回那个选择让用户换一条 ——
    // 实测「了」这个项目第一章第一个选项就是死路（n5 没有出边），
    // 旧逻辑只 toast 一句，用户以为「第一章到第二章没接线」，其实换个选项就能走下去。
    const back = [...pl.history].reverse()
      .map((id) => nodes.find((x) => x.id === id))
      .find((node) => node && node.type === 'choice' && choiceTargets(node).length > 1);
    if (back) {
      pl.history = pl.history.slice(0, pl.history.indexOf(back.id));
      toast('这条路还没写完整，回到上一个选择换一条');
      showNode(back.id, true);
      return;
    }
    toast('这个节点后面没接线了');
  });

  /** 选择点的按钮：只读 nodes[].choices（去掉占位槽），最多 4 个，按 condition 过滤。 */
  function choiceTargets(n) {
    let list = realChoices(n);
    if (list.length > 4) {
      console.error('[player] 选项数异常', n.id, list.length, '只显示前 4 个');
      list = list.slice(0, 4);
    }
    const shown = list.filter(passCondition);
    if (!shown.length && list.length) {
      console.warn('[player] 选项全被 condition 隐藏，回退为全部显示', n.id);
      return list;
    }
    return shown;
  }

  function showChoices(n) {
    const box = $('pChoiceBox');
    box.innerHTML = '';
    const targets = choiceTargets(n);
    if (!targets.length) {
      const b = document.createElement('button');
      b.textContent = '（这个分支还没接线）';
      b.addEventListener('click', (e) => e.stopPropagation());
      box.appendChild(b);
    }
    targets.forEach((c) => {
      const b = document.createElement('button');
      b.textContent = c.label;
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        $('pChoices').classList.remove('show');
        applyEffects(c);
        if (c.to) showNode(c.to);
        else toast('这个选项还没连到节点');
      });
      box.appendChild(b);
    });
    $('pChoices').classList.add('show');
  }

  function showEnding(n) {
    $('pDlg').style.display = 'none';
    $('pEnding').style.display = 'grid';
    $('pEndTitle').textContent = n.endKind === 'true' ? '真结局' : n.endKind === 'alt' ? '隐藏结局' : '坏结局';
    $('pEndTitle').style.color = n.endKind === 'true' ? '#f8fafc' : n.endKind === 'alt' ? '#a5f3fc' : '#fda4af';
    $('pEndSub').textContent = n.title;
  }

  /* ---------------- 接口配置：表单 ↔ 后端（字段名见 提示词-后端.md §3.3） ---------------- */
  const PROVIDER_BY_TITLE = [
    [/通义千问|千问/, 'qwen_image'],
    [/百炼|CosyVoice/i, 'bailian_tts'],
    [/大语言模型/, 'llm'],
    [/ComfyUI/i, 'comfyui'],
    [/本地 TTS/i, 'local_tts'],
    [/本地 LLM/i, 'local_llm']
  ];

  const FIELD_BY_LABEL = {
    qwen_image:  { 'API Key': 'api_key', '模型': 'model', '默认尺寸': 'size' },
    bailian_tts: { 'API Key': 'api_key', '音色': 'voice', '语速': 'speed' },
    llm:         { 'Base URL': 'base_url', 'API Key': 'api_key', '模型': 'model', '温度': 'temperature' },
    comfyui:     { '服务地址': 'base_url', '工作流': 'workflow_bg', '采样': 'sampler' },
    local_tts:   { '服务地址': 'base_url', '引擎': 'engine', '参考音频': 'ref_audio', '参考文本': 'ref_text' },
    local_llm:   { 'Base URL': 'base_url', '模型': 'model', '用途': 'usage' }
  };

  function providerOf(card) {
    const t = (card.querySelector('.api-head .t') || {}).textContent || '';
    for (const pair of PROVIDER_BY_TITLE) if (pair[0].test(t)) return pair[1];
    return null;
  }

  /** 后端 DEFAULTS 的字段白名单（多一个就 422），见 app/config.py */
  const BACKEND_FIELDS = {
    qwen_image:  ['enabled', 'api_key', 'model', 'base_url'],
    bailian_tts: ['enabled', 'api_key', 'model', 'voice', 'speed', 'base_url'],
    llm:         ['enabled', 'api_key', 'base_url', 'model', 'temperature'],
    comfyui:     ['enabled', 'base_url', 'workflow_bg', 'workflow_char', 'concurrency'],
    local_tts:   ['enabled', 'base_url', 'engine'],
    local_llm:   ['enabled', 'base_url', 'model']
  };

  /** 把表单值洗成后端能收的形状：去中文后缀、数值转 number、跳过掩码假 key */
  function normalizeProvider(kind, raw) {
    const allow = BACKEND_FIELDS[kind] || [];
    const out = {};
    allow.forEach((k) => {
      if (!(k in raw)) return;
      const v = raw[k];
      if (k === 'speed' || k === 'temperature') {
        const n = parseFloat(v);
        if (!isNaN(n)) out[k] = n;
        return;
      }
      if (typeof v === 'string') {
        if (k === 'api_key' && /^sk-[•*]/.test(v)) return;   // 预填的假 key 不写库
        const m = v.match(/^([^（(]+)/);                     // "longxiaochun（女·温柔）" -> "longxiaochun"
        const s = (m ? m[1] : v).trim();
        if ((k === 'base_url' || k === 'api_key' || k === 'model') && s === '') return;  // 空值不发，保留后端默认
        out[k] = s;
      } else {
        out[k] = v;
      }
    });
    return out;
  }

  /** 从卡片读值 → 契约里的 provider 配置对象 */
  function collectProviders() {
    const out = {};
    document.querySelectorAll('.api-card').forEach((card) => {
      const kind = providerOf(card);
      if (!kind) return;
      const map = FIELD_BY_LABEL[kind] || {};
      const obj = {};
      card.querySelectorAll('.field').forEach((f) => {
        const lab = ((f.querySelector('label') || {}).textContent || '').trim();
        const ctrl = f.querySelector('input, select');
        if (!ctrl || !lab) return;
        const hit = Object.keys(map).find((k) => lab.indexOf(k) === 0);
        if (hit) obj[map[hit]] = ctrl.value;
      });
      const NEED_KEY = ['qwen_image', 'bailian_tts', 'llm'];
      obj.enabled = NEED_KEY.indexOf(kind) >= 0
        ? !!(obj.api_key && String(obj.api_key).trim())
        : !!(obj.base_url && String(obj.base_url).trim());
      out[kind] = normalizeProvider(kind, obj);
    });
    return out;
  }

  function applyProviders(cfg) {
    if (!cfg) return;
    document.querySelectorAll('.api-card').forEach((card) => {
      const kind = providerOf(card);
      if (!kind || !cfg[kind]) return;
      const map = FIELD_BY_LABEL[kind] || {};
      card.querySelectorAll('.field').forEach((f) => {
        const lab = ((f.querySelector('label') || {}).textContent || '').trim();
        const ctrl = f.querySelector('input, select');
        if (!ctrl || !lab) return;
        const hit = Object.keys(map).find((k) => lab.indexOf(k) === 0);
        if (!hit) return;
        const v = cfg[kind][map[hit]];
        if (v == null || v === '***') return;   // 后端不回传密钥明文
        if (ctrl.tagName === 'SELECT') {
          const vals = Array.from(ctrl.options).map((o) => o.value);
          if (vals.indexOf(String(v)) < 0) return;   // 后端回来的值不在选项里就不动
        }
        ctrl.value = v;
      });
    });
  }

  async function loadProviders() {
    const cfg = await GalAPI.getProviders();
    if (cfg) { applyProviders(cfg); addLog('已载入接口配置'); }
  }

  async function saveProviders(silent) {
    const cfg = collectProviders();
    const r = await GalAPI.saveProviders(cfg);
    if (!silent) {
      if (r && r.ok) {
        addLog('✔ 接口配置已保存（' + Object.keys(cfg).length + ' 项）');
        toast('接口配置已保存');
      } else {
        addLog('✖ 保存接口配置失败：' + ((r && r.detail) || '未知'));
        toast('保存失败：' + ((r && r.detail) || '未知'));
      }
    }
    return r;
  }

  /* ---------------- 测试连接（mock 模拟 / real 真调） ---------------- */
  const KIND_BY_NAME = {
    '千问生图': 'qwen_image', '百炼 TTS': 'bailian_tts', 'LLM': 'llm',
    'ComfyUI': 'comfyui', '本地 TTS': 'local_tts', '本地 LLM': 'local_llm'
  };

  document.querySelectorAll('.test').forEach((b) => {
    b.addEventListener('click', async () => {
      const card = b.closest('.api-card');
      const hint = card.querySelector('.hint');
      const dot = card.querySelector('.dot');
      const name = b.dataset.name;
      const kind = KIND_BY_NAME[name] || 'llm';

      hint.className = 'hint';
      hint.textContent = '测试中…';
      dot.className = 'dot run';
      addLog('测试连接：' + name + '（' + kind + '）');

      const r = await GalAPI.testProvider(kind);
      if (r && r.ok) {
        dot.className = 'dot ok';
        hint.className = 'hint ok';
        hint.textContent = '连接正常 · ' + (r.latency_ms || 0) + 'ms';
        addLog('✔ ' + name + ' 正常 ' + (r.latency_ms || 0) + 'ms');
        toast(name + ' 连接正常');
      } else {
        dot.className = 'dot';
        hint.className = 'hint bad';
        hint.textContent = (r && r.detail) || '连接失败';
        addLog('✖ ' + name + ' 失败：' + ((r && r.detail) || '未知原因'));
        toast(name + ' 连不上');
      }
      updateGlobalMeta();
    });
  });

  /* ---------------- 模拟 / 真后端 切换 ---------------- */
  function updateModeUI() {
    const badge = $('modeBadge');
    const real = GalAPI.mode === 'real';
    badge.textContent = real ? (GalAPI.online ? '真后端' : '离线') : '模拟';
    badge.className = 'mode-badge' + (real ? (GalAPI.online ? ' real' : ' off') : '');
    document.querySelectorAll('#modeSwitch button').forEach((b) => {
      b.classList.toggle('on', b.dataset.mode === GalAPI.mode);
    });
  }

  document.querySelectorAll('#modeSwitch button').forEach((b) => {
    b.addEventListener('click', async () => {
      const m = b.dataset.mode;
      if (m === GalAPI.mode && m === 'mock') return;
      GalAPI.setMode(m);
      projectId = null;
      updateModeUI();

      if (m === 'real') {
        toast('正在探测 ' + GalAPI.base + ' …');
        addLog('探测后端 ' + GalAPI.base);
        const h = await GalAPI.detect();
        updateModeUI();
        if (h) {
          addLog('✔ 后端已连接 v' + (h.version || '?') + (h.mock ? '（mock 开关开着）' : ''));
          toast('后端已连接 v' + (h.version || '?'));
        } else {
          addLog('✖ 连不上后端 ' + GalAPI.base + (GalAPI.lastError ? '（' + GalAPI.lastError + '）' : ''));
          toast('连不上后端，生成会失败；可切回模拟数据');
        }
      } else {
        addLog('已切回模拟数据模式');
        toast('已切回模拟数据');
      }
    });
  });

  $('btnResetKeys').addEventListener('click', (e) => {
    // 破坏性操作要二次确认：有内容时才弹，空着直接清不啰嗦
    const inputs = document.querySelectorAll('.api-card input[type=password]');
    const hasValue = Array.prototype.some.call(inputs, (i) => !!i.value);
    if (hasValue) {
      popconfirm(e.currentTarget, '清空这一页填写的所有密钥？只清输入框，后端凭据文件不受影响。', () => {
        inputs.forEach((i) => { i.value = ''; });
        try { localStorage.removeItem('gal_providers'); } catch (err) {}
        addLog('已清空本页密钥');
        toast('已清空密钥（后端凭据文件没动，需在设置里删）');
      }, '清空');
      return;
    }
    inputs.forEach((i) => { i.value = ''; });
    try { localStorage.removeItem('gal_providers'); } catch (e) {}
    addLog('已清空本页密钥');
    toast('已清空密钥（后端凭据文件没动，需在设置里删）');
  });

  /* ---------------- 导出 ---------------- */
  document.querySelectorAll('.exp-btn').forEach((b) => {
    b.addEventListener('click', async () => {
      const fmt = b.dataset.format;
      const name = b.textContent;
      b.disabled = true;
      b.textContent = '导出中…';
      addLog('开始导出：' + name + '（' + fmt + '）');
      try {
        await ensureProjectId();
      } catch (e) {
        addLog('✖ 建项目失败：' + (e.message || e));
        toast('导出失败：建项目失败');
        b.disabled = false;
        b.textContent = name;
        return;
      }
      const r = await GalAPI.exportProject(projectId, fmt);
      b.disabled = false;
      b.textContent = name;
      if (r && r.ok) {
        addLog('✔ 导出完成：' + (r.out || fmt));
        if (r.warnings && r.warnings.length) {
          addLog('⚠ ' + r.warnings.join('；'));
          toast('导出完成，但有 ' + r.warnings.length + ' 张图是占位');
        } else {
          toast('导出完成');
        }
      } else {
        addLog('✖ 导出失败：' + ((r && r.detail) || '未知'));
        toast('导出失败：' + ((r && r.detail) || '未知'));
      }
    });
  });

  /* ---------------- 低功耗模式 ---------------- */
  function applyLowPower(on, quiet) {
    document.body.classList.toggle('low-power', on);
    const b = $('btnPerf');
    if (b) b.classList.toggle('on', on);
    try { localStorage.setItem('gal_lowpower', on ? '1' : '0'); } catch (e) {}
    if (!quiet) toast(on ? '低功耗：动画与毛玻璃已关闭' : '已恢复动效');
  }
  $('btnPerf').addEventListener('click', () => {
    applyLowPower(!document.body.classList.contains('low-power'));
  });

  /* ---------------- 启动 ---------------- */
  if (localStorage.getItem('gal_lowpower') === '1') applyLowPower(true, true);
  render();
  applyTransform();
  updateGlobalMeta();
  updateModeUI();
  updateHistoryUI();
  loadProviders();
  if (GalAPI.mode === 'real') {
    restoreLastProject().then((restored) => {
      updateModeUI();
      playDeepLink();          // 图装好之后再处理 ?player=1 深链接（见函数处注释）
      if (restored) return;
      GalAPI.detect().then((h) => {
        updateModeUI();
        addLog(h ? '✔ 后端在线 v' + (h.version || '?') : '✖ 后端离线：' + (GalAPI.lastError || GalAPI.base));
      });
    });
  }
  addLog('接口就绪：千问生图 / 百炼 TTS / DeepSeek；本地 ComfyUI 在线，本地 TTS 未启动');
  addLog('演示项目已载入：' + nodes.length + ' 节点 / ' + links.length + ' 连线（生成后换成后端数据）');

  // 支持 ?view=editor / ?view=export / &player=1 直接跳到某页（也方便截图自检）
  const qp = new URLSearchParams(location.search);

  if (qp.get('lowpower') === '1') applyLowPower(true, true);
  if (qp.get('mode') === 'real') {
    GalAPI.setMode('real');
    updateModeUI();
    GalAPI.detect().then(() => updateModeUI());
  }

  if (qp.get('modetest') === '1') {
    (async () => {
      const h = await GalAPI.detect();
      updateModeUI();
      const collected = collectProviders();
      let save = null;
      try { save = await saveProviders(true); } catch (e) { save = { ok: false, detail: String(e && e.message || e) }; }
      const back = await GalAPI.getProviders();
      document.title = 'MODETEST ' + JSON.stringify({
        mode: GalAPI.mode,
        badge: ($('modeBadge') || {}).textContent || '',
        health: h ? !!h.ok : null,
        save: save,
        providerKeys: back ? Object.keys(back) : null,
        sentKeys: Object.keys(collected).map((k) => k + ':' + Object.keys(collected[k]).join('|'))
      });
    })();
  }

  if (qp.get('exporttest') === '1') {
    (async () => {
      try {
        await ensureProjectId();
        const r = await GalAPI.exportProject(projectId, 'renpy');
        document.title = 'EXPORTTEST ' + JSON.stringify(r);
      } catch (e) {
        document.title = 'EXPORTTEST {"ok":false,"detail":"' + String(e.message || e).replace(/"/g, "'") + '"}';
      }
    })();
  }

  if (qp.get('selftest') === '1') {
    document.title = 'SELFTEST ' + JSON.stringify({
      providers: collectProviders(),
      nodes: nodes.length,
      links: links.length,
      mode: GalAPI.mode,
      expBtns: document.querySelectorAll('.exp-btn').length,
      cards: document.querySelectorAll('.api-card').length
    });
  }

  if (qp.get('undotest') === '1') {
    const before = nodes.length;
    pushHistory('测试加节点');
    nodes.push({ id: 'ntest', type: 'scene', idx: '99', title: '测试节点', x: 0, y: 0, who: '', text: 't', bg: 'rainy', voice: 1 });
    const afterAdd = nodes.length;
    undo();
    const afterUndo = nodes.length;
    redo();
    const afterRedo = nodes.length;
    document.title = 'UNDOTEST ' + JSON.stringify({
      before: before, afterAdd: afterAdd, afterUndo: afterUndo, afterRedo: afterRedo,
      undoBtnOk: !!document.getElementById('btnUndo'),
      redoBtnOk: !!document.getElementById('btnRedo')
    });
  }

  if (qp.get('layouttest') === '1') {
    setTimeout(() => {
      let worldRight = 0, worldBottom = 0;
      nodes.forEach((n) => {
        const el = nodeEl(n.id);
        worldRight = Math.max(worldRight, n.x + 268);
        worldBottom = Math.max(worldBottom, n.y + (el ? el.offsetHeight : 190));
      });
      const rects = {};
      ['n1', 'n6', 'n7', 'n9'].forEach((id) => {
        const el = nodeEl(id);
        if (!el) { rects[id] = null; return; }
        const r = el.getBoundingClientRect();
        rects[id] = { w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left) };
      });
      document.title = 'LAYOUTTEST ' + JSON.stringify({
        zoom: +zoom.toFixed(3), panX: Math.round(panX), panY: Math.round(panY),
        wrapW: wrap.clientWidth, wrapH: wrap.clientHeight,
        worldRight: worldRight, worldBottom: worldBottom,
        screenRight: Math.round(panX + worldRight * zoom),
        screenBottom: Math.round(panY + worldBottom * zoom),
        overflowX: panX + worldRight * zoom > wrap.clientWidth,
        overflowY: panY + worldBottom * zoom > wrap.clientHeight,
        rects: rects,
        rawW: nodeEl('n7') ? nodeEl('n7').offsetWidth : -1
      });
    }, 200);
  }

  if (qp.get('autogen') === '1') {
    setTimeout(async () => {
      try { await generate(); } catch (e) { /* 失败也照常上报 */ }
      const logs = ($('logBox').textContent || '').trim().split('\n').slice(-4);
      document.title = 'AUTOGEN ' + JSON.stringify({
        mode: GalAPI.mode,
        nodes: nodes.length,
        links: links.length,
        genLabel: ($('genLabel') || {}).textContent || '',
        logs: logs
      });
    }, 400);
  }

  if (qp.get('playtest') === '1') {
    setTimeout(() => {
      openPlayer();
      const start = startNodeId();
      const e1 = links.find((l) => l.from === start);
      if (e1) showNode(e1.to);
      const e2 = links.find((l) => l.from === pl.cur);
      if (e2) showNode(e2.to);
      const afterFwd = pl.cur;
      const histLen = pl.history.length;
      $('plBack').click();
      const afterBack = pl.cur;
      $('plRestart').click();
      const afterRestart = pl.cur;
      document.title = 'PLAYTEST ' + JSON.stringify({
        start: start, afterFwd: afterFwd, histLen: histLen,
        afterBack: afterBack, afterRestart: afterRestart,
        backBtn: !!$('plBack'), restartBtn: !!$('plRestart')
      });
    }, 500);
  }

  if (qp.get('edittest') === '1') {
    setTimeout(() => {
      const n = nodes.find((x) => x.type === 'scene') || nodes[0];
      const el = nodeEl(n.id);
      const oldText = n.text;
      const histBefore = hist.undo.length;
      const lineEl = el.querySelector('.line');
      if (lineEl) lineEl.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
      const ta = el.querySelector('.inline-edit');
      const hasTa = !!ta;
      if (ta) {
        ta.value = '改过的台词测试';
        ta.dispatchEvent(new Event('blur'));
      }
      const afterText = (nodes.find((x) => x.id === n.id) || {}).text;
      document.title = 'EDITTEST ' + JSON.stringify({
        node: n.id, hasTa: hasTa, oldText: oldText, afterText: afterText,
        histDelta: hist.undo.length - histBefore,
        taGone: !el.querySelector('.inline-edit')
      });
    }, 400);
  }

  if (qp.get('linktest') === '1') {
    setTimeout(() => {
      const before = links.length;

      addNodeOfType('scene');
      const newNode = nodes[nodes.length - 1];
      const newId = newNode.id;
      const nodesAfterAdd = nodes.length;
      const newType = newNode.type;

      links.push({ from: newId, port: 0, to: nodes[1].id });
      drawWires();
      const afterLink = links.length;

      selLink = links.findIndex((l) => l.from === newId);
      const selOk = selLink >= 0;
      deleteLink();
      const afterUnlink = links.length;
      const stillHas = links.some((l) => l.from === newId);

      addNodeOfType('choice');
      const ch = nodes[nodes.length - 1];
      addNodeOfType('ending');
      const end = nodes[nodes.length - 1];

      document.title = 'LINKTEST ' + JSON.stringify({
        linksBefore: before,
        nodesAfterAdd: nodesAfterAdd,
        newType: newType,
        afterLink: afterLink,
        selOk: selOk,
        afterUnlink: afterUnlink,
        stillHas: stillHas,
        choiceOuts: (ch.choices || []).length,
        choicePorts: document.querySelectorAll('#nd-' + ch.id + ' .port.out').length,
        endKind: end.endKind,
        endPorts: document.querySelectorAll('#nd-' + end.id + ' .port.out').length
      });
    }, 400);
  }

  if (qp.get('menu') === '1') {
    setTimeout(() => { $('btnAdd').click(); }, 300);
  }

  if (qp.get('cuttest') === '1') {
    setTimeout(() => {
      const na = nodes.find((x) => x.id === 'n1');
      const nb = nodes.find((x) => x.id === 'n2');
      const p1 = outPos(na, 0), p2 = inPos(nb);
      const pts = bezierPoints(p1, p2, 24);
      const mid = pts[12];
      const hitA = { x: mid.x, y: mid.y - 50 }, hitB = { x: mid.x, y: mid.y + 50 };
      let hit = false;
      for (let k = 0; k < pts.length - 1; k++) {
        if (segIntersect(pts[k], pts[k + 1], hitA, hitB)) { hit = true; break; }
      }
      const misA = { x: mid.x + 400, y: mid.y - 50 }, misB = { x: mid.x + 400, y: mid.y + 50 };
      let miss = false;
      for (let k = 0; k < pts.length - 1; k++) {
        if (segIntersect(pts[k], pts[k + 1], misA, misB)) { miss = true; break; }
      }
      document.title = 'CUTTEST ' + JSON.stringify({ hit: hit, missHit: miss, points: pts.length });
    }, 300);
  }
  if (qp.get('savetest') === '1') {
    setTimeout(async () => {
      const info = { mode: GalAPI.mode, projectId: projectId };
      if (!projectId) {
        document.title = 'SAVETEST ' + JSON.stringify(Object.assign(info, { err: '没有项目' }));
        return;
      }
      try {
        await pullGraphFromBackend();
        const n0 = nodes[0];
        if (!n0) {
          document.title = 'SAVETEST ' + JSON.stringify(Object.assign(info, { err: '没有节点' }));
          return;
        }
        const beforePos = { x: n0.x, y: n0.y };
        const beforeLines = (n0._raw && Array.isArray(n0._raw.lines)) ? n0._raw.lines.length : 0;
        const targetX = Math.round(beforePos.x) + 77;
        const targetY = Math.round(beforePos.y) + 33;
        n0.x = targetX;
        n0.y = targetY;
        let putRes = null;
        let putErr = '';
        try {
          const pl = buildGraphPayload();
          delete pl._fixes;          // 调试字段，不能进请求体
          putRes = await GalAPI.saveGraph(projectId, pl);
        } catch (e2) {
          putErr = String((e2 && e2.message) || e2);
        }
        const ok = !!(putRes && putRes.ok);
        const putDetail = putErr || (putRes && putRes.detail) || '';
        const putStatus = (putRes && putRes.status) || 0;
        const payloadProject = (graphProject && graphProject.id) || null;
        await pullGraphFromBackend();
        const n1 = nodes.filter((x) => x.id === n0.id)[0];
        const afterPos = n1 ? { x: n1.x, y: n1.y } : null;
        const afterLines = (n1 && n1._raw && Array.isArray(n1._raw.lines)) ? n1._raw.lines.length : 0;
        let posMatched = false;
        if (afterPos) {
          posMatched = Math.abs(afterPos.x - targetX) < 2 && Math.abs(afterPos.y - targetY) < 2;
        }
        document.title = 'SAVETEST ' + JSON.stringify({
          saveOk: ok,
          putStatus: putStatus,
          putDetail: putDetail,
          payloadProjectId: payloadProject,
          projectId: projectId,
          beforePos: beforePos,
          afterPos: afterPos,
          targetPos: { x: targetX, y: targetY },
          posMatched: posMatched,
          beforeLines: beforeLines,
          afterLines: afterLines,
          linesKept: afterLines >= beforeLines && afterLines > 0
        });
      } catch (err) {
        document.title = 'SAVETEST ' + JSON.stringify(Object.assign(info, { err: String(err && err.message || err) }));
      }
    }, 6500);
  }

  if (qp.get('porttest') === '1') {
    setTimeout(() => {
      const n = nodes[1] || nodes[0];
      const el = nodeEl(n.id);
      const port = el ? el.querySelector('.port.out') : null;
      const info = {
        node: n.id,
        portFound: !!port,
        linkingBefore: !!linking,
        pendingBefore: !!pendingFrom
      };
      if (port) {
        const r = port.getBoundingClientRect();
        info.portSize = { w: Math.round(r.width), h: Math.round(r.height) };
        info.portCenter = { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
        info.hitElement = (function () {
          const e = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
          if (!e) return 'null';
          return e.className + (e.id ? '#' + e.id : '');
        })();
        // 热区检查：偏离中心 8px 还能不能点中端口
        (function () {
          const cs = getComputedStyle(port, '::before');
          info.before = {
            display: cs.display, position: cs.position,
            inset: cs.inset, width: cs.width, height: cs.height,
            pe: cs.pointerEvents
          };
          const ns = getComputedStyle(el);
          info.nodeOverflow = ns.overflow;
          info.nodeZ = ns.zIndex;
          const portRect = port.getBoundingClientRect();
          info.portRect = { x: Math.round(portRect.x), y: Math.round(portRect.y), w: Math.round(portRect.width), h: Math.round(portRect.height) };
        })();
        info.hitOffset8 = (function () {
          const e = document.elementFromPoint(r.x + r.width / 2 + 8, r.y + r.height / 2);
          if (!e) return 'null';
          return String(e.className || e.tagName);
        })();
        info.hitOffset12 = (function () {
          const e = document.elementFromPoint(r.x + r.width / 2 + 12, r.y + r.height / 2);
          if (!e) return 'null';
          return String(e.className || e.tagName);
        })();
        port.dispatchEvent(new PointerEvent('pointerdown', {
          bubbles: true, cancelable: true, button: 0, pointerId: 1,
          clientX: r.x + r.width / 2, clientY: r.y + r.height / 2
        }));
        info.linkingAfter = !!linking;
        info.tempPaths = document.querySelectorAll('.wire-temp').length;
        // 收尾，避免残留
        document.dispatchEvent(new PointerEvent('pointerup', {
          bubbles: true, button: 0, pointerId: 1,
          clientX: r.x + r.width / 2, clientY: r.y + r.height / 2
        }));
        info.linkingAfterUp = !!linking;
        info.pendingAfterUp = !!pendingFrom;
      }
      document.title = 'PORTTEST ' + JSON.stringify(info);
    }, 500);
  }

  if (qp.get('zoomtest') === '1') {
    setTimeout(() => {
      const z0 = GalEditor.getZoom();
      document.getElementById('zoomIn').click();
      document.getElementById('zoomIn').click();
      document.getElementById('zoomIn').click();
      const z1 = GalEditor.getZoom();
      document.getElementById('zoomOut').click();
      const z2 = GalEditor.getZoom();
      const label = document.getElementById('zoomInfo').textContent;
      document.title = 'ZOOMTEST ' + JSON.stringify({
        z0: Math.round(z0 * 100),
        z1: Math.round(z1 * 100),
        z2: Math.round(z2 * 100),
        label: label
      });
    }, 800);
  }

  if (qp.get('visittest') === '1') {
    // 模拟真实流程：先在配置页（编排页 display:none），再切到编排页
    setTimeout(function () {
      go('setup');
      setTimeout(function () {
        go('editor');
        setTimeout(function () {
          var cv = document.getElementById('galCanvas');
          var rect = cv.getBoundingClientRect();
          document.title = 'VISITTEST ' + JSON.stringify({
            cvAttrW: cv.width, cvAttrH: cv.height,
            cvCssW: Math.round(rect.width), cvCssH: Math.round(rect.height),
            clientW: cv.clientWidth, clientH: cv.clientHeight,
            zoom: Math.round(GalEditor.getZoom() * 100),
            nodes: GalEditor.dump().nodes.length,
            links: GalEditor.dump().links.length
          });
        }, 500);
      }, 250);
    }, 600);
  }

  if (qp.get('wheeltest') === '1') {
    // 验证真实路径：只用标准 wheel 事件（现代浏览器真实派发的类型），
    // 且必须 bubbles=true —— 走完 canvas → wrap 的完整冒泡链，
    // 这样才能抓出「两套滚轮逻辑打架」。
    setTimeout(function () {
      var cv = document.getElementById('galCanvas');
      var rect = cv.getBoundingClientRect();
      var cx = 260, cy = 180;               // canvas 内一点
      var g0 = GalEditor.screenToGraph(cx, cy);
      var z0 = GalEditor.getZoom();

      function fire(dy) {
        cv.dispatchEvent(new WheelEvent('wheel', {
          bubbles: true, cancelable: true,
          clientX: rect.left + cx, clientY: rect.top + cy,
          deltaY: dy, deltaMode: 0
        }));
      }

      fire(-100);                            // 向上 = 放大
      var g1 = GalEditor.screenToGraph(cx, cy);
      var z1 = GalEditor.getZoom();

      fire(100);                             // 向下 = 缩小，应回到原点
      var g2 = GalEditor.screenToGraph(cx, cy);
      var z2 = GalEditor.getZoom();

      var dx1 = Math.abs(g1.x - g0.x), dy1 = Math.abs(g1.y - g0.y);
      var dx2 = Math.abs(g2.x - g0.x), dy2 = Math.abs(g2.y - g0.y);
      // 一次滚轮只该缩 1.12 倍；如果看到约 1.12*1.1≈1.23 就是两套逻辑在打架
      var ratio = +(z1 / z0).toFixed(4);

      document.title = 'WHEELTEST ' + JSON.stringify({
        z0: Math.round(z0 * 100),
        z1: Math.round(z1 * 100),
        z2: Math.round(z2 * 100),
        ratio: ratio,
        singleStep: Math.abs(ratio - 1.12) < 0.005,
        driftX: Math.round(dx1 * 100) / 100,
        driftY: Math.round(dy1 * 100) / 100,
        driftBackX: Math.round(dx2 * 100) / 100,
        driftBackY: Math.round(dy2 * 100) / 100,
        focalStable: dx1 < 0.6 && dy1 < 0.6 && dx2 < 0.6 && dy2 < 0.6,
        pass: Math.abs(ratio - 1.12) < 0.005 && dx1 < 0.6 && dy1 < 0.6 && dx2 < 0.6 && dy2 < 0.6
      });
    }, 900);
  }

  if (qp.get('uitest') === '1') {
    // 综合自测：滚轮焦点 / 旧 CSS 变换是否污染 / 节点能否拖动
    setTimeout(function () {
      var cv = document.getElementById('galCanvas');
      var wrapEl = document.getElementById('canvasWrap');
      var worldEl = document.getElementById('world');
      var rect = cv.getBoundingClientRect();
      var cvs = GalEditor._canvas();
      var ds = cvs.ds;

      var out = {
        canvasW: Math.round(rect.width),
        canvasH: Math.round(rect.height),
        zoom: Math.round(GalEditor.getZoom() * 100),
        nodes: cvs.graph._nodes.length
      };

      // 1) world 上不许残留旧的 translate/scale
      out.worldTransform = (worldEl.style.transform || '').trim();
      out.worldClean = out.worldTransform === '' || out.worldTransform === 'none';

      // 2) 三处不同位置滚轮，焦点必须零漂移，每次只缩 1.12
      var pts = [[120, 140], [420, 300], [700, 180]];
      var drifts = [], ratios = [];
      pts.forEach(function (p) {
        var g0 = GalEditor.screenToGraph(p[0], p[1]);
        var z0 = GalEditor.getZoom();
        cv.dispatchEvent(new WheelEvent('wheel', {
          bubbles: true, cancelable: true,
          clientX: rect.left + p[0], clientY: rect.top + p[1],
          deltaY: -100, deltaMode: 0
        }));
        var g1 = GalEditor.screenToGraph(p[0], p[1]);
        var z1 = GalEditor.getZoom();
        drifts.push(Math.round(Math.hypot(g1.x - g0.x, g1.y - g0.y) * 100) / 100);
        ratios.push(+(z1 / z0).toFixed(4));
        // 缩回去，保持每次起点一致
        cv.dispatchEvent(new WheelEvent('wheel', {
          bubbles: true, cancelable: true,
          clientX: rect.left + p[0], clientY: rect.top + p[1],
          deltaY: 100, deltaMode: 0
        }));
      });
      out.wheelDrifts = drifts;
      out.wheelRatios = ratios;
      out.wheelOk = drifts.every(function (d) { return d < 0.6; }) &&
                    ratios.every(function (r) { return Math.abs(r - 1.12) < 0.005; });

      // 3) 拖节点：pointerdown → pointermove → pointerup，位置必须真的动
      var node = cvs.graph._nodes[0];
      var before = [node.pos[0], node.pos[1]];
      function toScreen(gx, gy) {
        // canvasPx = (graph + offset) * scale
        return { x: (gx + ds.offset[0]) * ds.scale, y: (gy + ds.offset[1]) * ds.scale };
      }
      var whichProbe = [];
      // window 捕获阶段比 canvas 更早，在这里补 which 才来得及影响 LiteGraph
      ['mousedown', 'mousemove', 'mouseup'].forEach(function (t) {
        window.addEventListener(t, function (e) {
          try {
            Object.defineProperty(e, 'which', { value: 1, configurable: true, writable: true });
            whichProbe.push(e.which);
          } catch (err) { whichProbe.push('ERR:' + ((err && err.name) || '')); }
        }, true);
      });

      function pe(type, x, y, buttons) {
        // LiteGraph 0.7.18 的 pointerevents_method 默认是 "mouse"，
        // 所以它绑的是 mousedown/mousemove/mouseup，测试必须用 MouseEvent。
        cv.dispatchEvent(new MouseEvent(type, {
          bubbles: true, cancelable: true,
          button: 0, buttons: buttons,
          clientX: rect.left + x, clientY: rect.top + y
        }));
      }
      var s = toScreen(before[0] + node.size[0] / 2, before[1] + 18); // 标题栏位置
      out.nodePos = [Math.round(before[0]), Math.round(before[1])];
      out.nodeSize = [Math.round(node.size[0]), Math.round(node.size[1])];
      out.dsScale = +ds.scale.toFixed(3);
      out.dsOffset = [Math.round(ds.offset[0]), Math.round(ds.offset[1])];
      out.downAt = [Math.round(s.x), Math.round(s.y)];
      // 直接问 LiteGraph：这个 canvas 坐标上是谁？（不同版本签名不同，出错不影响主流程）
      try {
        var hit = cvs.getNodeOnPos ? cvs.getNodeOnPos(s.x, s.y) : null;
        out.hitId = hit ? hit.id : null;
        var hitG = cvs.getNodeOnPos ? cvs.getNodeOnPos(before[0] + node.size[0] / 2, before[1] + 18) : null;
        out.hitGraph = hitG ? hitG.id : null;
      } catch (e1) { out.hitErr = String(e1 && e1.message || e1).slice(0, 70); }
      out.visibleNodes = cvs.visible_nodes ? cvs.visible_nodes.length : -1;
      out.whichProbe = whichProbe;
      out.hitExpected = node.id;
      pe('mousedown', s.x, s.y, 1);
      out.afterDown = {
        dragged: !!cvs.node_dragged,
        draggedId: cvs.node_dragged ? cvs.node_dragged.id : null,
        selected: cvs.selected_nodes ? cvs.selected_nodes.length : -1,
        pointerDown: !!cvs.pointer_is_down
      };
      pe('mousemove', s.x + 60, s.y + 40, 1);
      out.afterMove = { dragged: !!cvs.node_dragged, pos: [Math.round(node.pos[0]), Math.round(node.pos[1])] };
      pe('mouseup', s.x + 60, s.y + 40, 0);
      var after = [node.pos[0], node.pos[1]];
      out.dragDX = Math.round(after[0] - before[0]);
      out.dragDY = Math.round(after[1] - before[1]);
      out.dragOk = Math.abs(out.dragDX) > 10 && Math.abs(out.dragDY) > 5;

      out.pass = out.worldClean && out.wheelOk && out.dragOk &&
                 out.canvasW > 100 && out.nodes > 0;
      document.title = 'UITEST ' + JSON.stringify(out);
    }, 1400);
  }

  if (qp.get('fitprobe') === '1') {
    // 查「适应视图」到底算出了什么（结果同时写 title，http 下可直接 dump 读到）
    setTimeout(function () {
      var parts = [];
      function P(k, v) { parts.push(k + '=' + v); }
      try {
        var cvEl = document.getElementById('galCanvas');
        P('hasCanvasEl', !!cvEl);
        if (cvEl) {
          P('attr', cvEl.width + 'x' + cvEl.height);
          P('css', Math.round(cvEl.getBoundingClientRect().width) + 'x' + Math.round(cvEl.getBoundingClientRect().height));
          P('client', cvEl.clientWidth + 'x' + cvEl.clientHeight);
        }
        var c = (window.GalEditor && GalEditor._canvas) ? GalEditor._canvas() : null;
        P('canvasObj', !!c);
        if (c) {
          P('scale', +(c.ds.scale || 0).toFixed(4));
          P('offset', Math.round(c.ds.offset[0]) + ',' + Math.round(c.ds.offset[1]));
          P('graph', !!c.graph);
          if (c.graph) {
            P('n', c.graph._nodes.length);
            if (c.graph._nodes.length) {
              var g0 = c.graph._nodes[0];
              P('n0pos', Math.round(g0.pos[0]) + ',' + Math.round(g0.pos[1]));
              P('n0size', Math.round(g0.size[0]) + 'x' + Math.round(g0.size[1]));
              P('n0cvs', Math.round(c.ds.convertOffsetToCanvas([g0.pos[0], g0.pos[1]])[0]) + ',' +
                          Math.round(c.ds.convertOffsetToCanvas([g0.pos[0], g0.pos[1]])[1]));
            }
          }
        }
        var fl = window.__fitLog;
        P('fitLog', fl ? (fl.w + 'x' + fl.h + ' gw' + fl.gw + ' s' + fl.scale + ' ok' + fl.ok) : 'null');
        P('win', window.innerWidth + 'x' + window.innerHeight);
        P('dpr', window.devicePixelRatio);
        var ed = document.querySelector('.editor');
        var cw = document.querySelector('.canvas-wrap');
        P('editor', ed ? Math.round(ed.getBoundingClientRect().width) : 'x');
        P('wrap', cw ? (Math.round(cw.getBoundingClientRect().width) + '@' + Math.round(cw.getBoundingClientRect().left)) : 'x');
        var cv2 = GalEditor._canvas();
        if (cv2) {
          P('cvsAttr', cv2.canvas.width + 'x' + cv2.canvas.height);
          P('sameBg', cv2.bgcanvas === cv2.canvas);
          P('bgAttr', cv2.bgcanvas.width + 'x' + cv2.bgcanvas.height);
          P('visN', cv2.visible_nodes ? cv2.visible_nodes.length : -1);
          P('visArea', cv2.ds.visible_area ? cv2.ds.visible_area.map(Math.round).join(',') : 'none');
          P('hasCtx', !!cv2.ctx);
          if (cv2.ctx) {
            var m = cv2.ctx.getTransform();
            P('tf', [m.a, m.b, m.c, m.d, Math.round(m.e), Math.round(m.f)].map(function (v) { return typeof v === 'number' ? +v.toFixed(2) : v; }).join(','));
          }
          P('drawn', cv2.frame === undefined ? 'na' : cv2.frame);
          P('nodeErr', window.__nodeErr ? window.__nodeErr.slice(0, 160) : 'none');
          // 采一块画布像素，判断到底画没画东西（背景是浅灰，卡片/文字会比它深）
          try {
            var c2 = cv2.ctx.canvas.getContext('2d');
            var img = c2.getImageData(0, 0, Math.min(400, cv2.ctx.canvas.width), Math.min(400, cv2.ctx.canvas.height)).data;
            var dark = 0, total = img.length / 4;
            for (var q = 0; q < img.length; q += 4) {
              if (img[q] < 200 && img[q + 1] < 200 && img[q + 2] < 200) dark++;
            }
            P('darkRatio', +(dark / total).toFixed(4));
          } catch (e3) { P('pxErr', String(e3 && e3.message).slice(0, 60)); }
        }
      } catch (e) {
        P('ERR', String(e && e.message || e).slice(0, 80));
      }
      document.title = 'FITPROBE ' + parts.join(' ');
      // 把画布真实内容导出成图片，绕开「截图可能合成不到 canvas」的问题
      try {
        var cc = GalEditor._canvas();
        if (cc && cc.ctx && cc.ctx.canvas && cc.ctx.canvas.toDataURL) {
          var im = document.createElement('img');
          im.id = 'cvshot';
          im.src = cc.ctx.canvas.toDataURL('image/png');
          document.body.appendChild(im);
        }
      } catch (e4) { /* 忽略 */ }
    }, 2500);
  }

  if (qp.get('lodtest') === '1') {
    setTimeout(function () { GalEditor.setZoom(0.45); GalEditor.redraw(); }, 900);
  }

  const qv = qp.get('view');
  if (qv && views[qv]) go(qv);

  /** 试玩深链接：?player=1[&node=<id>][&choices=1]
   *
   *  ★ 不能在这里直接播 —— `restoreLastProject()` 是异步的，这一段同步代码跑的时候
   *  图还没装好。实测（2026-11-10）：带 node=n90 打开，播放器显示的是 9 节点的
   *  演示项目、总页数「1 / 9」。所以真后端模式下由 restore 的 then 回调来调本函数。
   */
  function playDeepLink() {
    if (qp.get('player') !== '1') return;
    openPlayer();
    const nid = qp.get('node');
    if (!nid) return;
    if (!nodes.some((x) => x.id === nid)) {
      addLog('试玩深链接：当前项目里没有节点 ' + nid + '，从开头播');
      return;
    }
    showNode(nid);
    if (qp.get('choices') === '1') {
      const n = nodes.find((x) => x.id === nid);
      if (n && n.type === 'choice') { pl.step = 'choice'; showChoices(n); }
    }
  }
  if (GalAPI.mode !== 'real') playDeepLink();   // 模拟模式没有异步恢复，直接播
})();
