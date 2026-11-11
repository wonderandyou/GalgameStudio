/* 奇迹工坊 · 参考图 / 语音 / 素材库 / Unity 导出（无构建） */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var state = { kind: 'character', refs: [], voices: [], library: null, lib: 'roles', chars: [], assignments: {}, graphReady: true, busy: false };
  var qp = new URLSearchParams(location.search);

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]; }); }
  // 不再退回 'demo'：拿不到项目就是没打开项目，由调用方显示空状态。
  // 硬退 'demo' 会让人以为在看真实数据，正是"素材库像全局功能"的根源。
  function pid() { return qp.get('pid') || window.__galProjectId || (window.GalFrontendTest && GalFrontendTest.currentProject()) || ''; }
  function toast(s) { var t = $('toast'); if (!t) return; t.textContent = s; t.classList.add('show'); clearTimeout(t._at); t._at = setTimeout(function () { t.classList.remove('show'); }, 3200); }

  // 应用内确认框（Promise 版）。
  // ⚠ 本项目 app.js 把 window.confirm 接管了：只弹一条 toast 提示、且**恒返回 false**
  //   （见 app.js installNativeDialogGuard）。所以拿 window.confirm 当确认框 = 永远点不了「确定」。
  //   这里自己画遮罩 + 两个按钮，返回 Promise<boolean>。
  //   故意不绑 Enter：删东西不可逆，只允许明确点按钮或按 Esc 取消。
  function askConfirm(message, okText, danger) {
    return new Promise(function (resolve) {
      var mask = document.createElement('div');
      mask.className = 'gal-confirm-mask';
      var box = document.createElement('div');
      box.className = 'gal-confirm';
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-modal', 'true');
      var msg = document.createElement('div');
      msg.className = 'gal-confirm-msg';
      msg.textContent = message;
      var acts = document.createElement('div');
      acts.className = 'gal-confirm-acts';
      var no = document.createElement('button');
      no.type = 'button'; no.className = 'gal-confirm-btn ghost'; no.textContent = '取消';
      var yes = document.createElement('button');
      yes.type = 'button';
      yes.className = 'gal-confirm-btn' + (danger ? ' danger' : '');
      yes.textContent = okText || '确定';
      acts.appendChild(no); acts.appendChild(yes);
      box.appendChild(msg); box.appendChild(acts); mask.appendChild(box);
      document.body.appendChild(mask);

      var done = false;
      function finish(value) {
        if (done) return;
        done = true;
        document.removeEventListener('keydown', onKey, true);
        if (mask.parentNode) mask.parentNode.removeChild(mask);
        resolve(value);
      }
      function onKey(ev) {
        if (ev.key === 'Escape') { ev.stopPropagation(); finish(false); }
      }
      no.addEventListener('click', function () { finish(false); });
      yes.addEventListener('click', function () { finish(true); });
      mask.addEventListener('click', function (ev) { if (ev.target === mask) finish(false); });
      document.addEventListener('keydown', onKey, true);
      setTimeout(function () { try { yes.focus(); } catch (e) {} }, 30);
    });
  }
  function bytes(n) { n = +n || 0; return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }
  function secs(n) { n = +n || 0; return n > 0 ? n.toFixed(2) + ' 秒' : '时长读取中'; }
  function showError(id, err) { var el = $(id); if (!el) return; el.hidden = false; el.textContent = (err.code ? '[' + err.code + '] ' : '') + (err.message || String(err)); }
  function clearError(id) { var el = $(id); if (el) { el.hidden = true; el.textContent = ''; } }
  function url(item) { return GalAPI.assetURL(pid(), item.asset || ''); }
  // 角色库是唯一来源：不在角色库里的 id 就是悬空引用（角色被删了），
  // 这时不能再把内部 id 甩给用户看（以前会显示「heroine」这种字符串）。
  function charName(id) {
    if (!id) return '未标注角色';
    var c = state.chars.filter(function (x) { return x.id === id; })[0];
    return c ? (c.name || c.display_name || c.id) : '未标注角色';
  }

  function characterOptions(selected) {
    return '<option value="">未标注角色</option>' + state.chars.map(function (c) {
      return '<option value="' + esc(c.id) + '"' + (c.id === selected ? ' selected' : '') + '>' + esc(c.name || c.display_name || c.id) + '</option>';
    }).join('');
  }

  function renderCharacters() {
    var old = $('voiceCharacter').value;
    $('voiceCharacter').innerHTML = characterOptions(old);
    // 参考图上传区的「这是谁」也来自角色库
    var refSel = $('refCharacter');
    if (refSel) {
      var oldRef = refSel.value;
      refSel.innerHTML = '<option value="">不指定角色（传完再选也行）</option>' +
        (state.chars || []).map(function (c) {
          return '<option value="' + esc(c.id) + '"' + (c.id === oldRef ? ' selected' : '') + '>' +
            esc(c.name || c.display_name || c.id) + '</option>';
        }).join('');
    }
    renderVoices();
  }

  // 只有「角色立绘参考」需要选人，场景风格参考不需要 —— 切分组时跟着显隐
  function syncRefCharPicker() {
    var sel = $('refCharacter'), hint = $('refCharHint');
    var isChar = state.kind === 'character';
    if (sel) sel.hidden = !isChar;
    if (hint) hint.hidden = !isChar;
  }

  function readFile(file) {
    return new Promise(function (resolve, reject) {
      var r = new FileReader(); r.onload = function () { resolve(r.result); }; r.onerror = function () { reject(new Error('浏览器读取文件失败')); }; r.readAsDataURL(file);
    });
  }

  function audioSeconds(file) {
    return new Promise(function (resolve) {
      var a = document.createElement('audio'); var u = URL.createObjectURL(file);
      a.preload = 'metadata'; a.onloadedmetadata = function () { var s = a.duration; URL.revokeObjectURL(u); resolve(isFinite(s) ? s : 0); };
      a.onerror = function () { URL.revokeObjectURL(u); resolve(0); }; a.src = u;
    });
  }

  function validate(file, type) {
    if (!file) return '没有选择文件';
    if (file.size > 20 * 1024 * 1024) return 'FILE_TOO_LARGE：「' + file.name + '」超过 20 MB（实际 ' + bytes(file.size) + '）';
    var ext = (file.name.split('.').pop() || '').toLowerCase();
    // 语音只收 PCM WAV：后端 POST /voices 对 mp3/ogg/m4a 一律 415 NEED_WAV（当前环境只装了 WAV 解码）。
    // 拖拽也走这里，所以白名单必须和 accept 一起收窄，否则拖进来的 mp3 会被后端拒。
    var allowed = type === 'ref' ? ['png','jpg','jpeg'] : ['wav'];
    if (allowed.indexOf(ext) < 0) return (type === 'ref' ? 'INVALID_IMAGE' : 'INVALID_AUDIO') + '：「' + file.name + '」格式不支持，仅接受 ' + allowed.join(' / ');
    if (!file.size) return 'EMPTY_FILE：「' + file.name + '」是空文件';
    return '';
  }

  function renderRefs() {
    ['character','scene'].forEach(function (kind) {
      var arr = state.refs.filter(function (x) { return x.kind === kind; });
      var box = $(kind === 'character' ? 'characterRefs' : 'sceneRefs');
      box.innerHTML = arr.length ? arr.map(function (x) {
        var src = url(x);
        return '<div class="ref-item" data-id="' + esc(x.id) + '">' +
          (src ? '<img class="ref-thumb" src="' + esc(src) + '" alt="' + esc(x.label) + '">' : '<div class="ref-thumb"></div>') +
          '<div class="ref-info"><b title="' + esc(x.label) + '">' + esc(x.label || x.id) + '</b><span>' + bytes(x.bytes) + (x.width ? ' · ' + x.width + '×' + x.height : '') + '</span></div>' +
          '<button class="asset-del" title="删除参考图" aria-label="删除">×</button></div>';
      }).join('') : '<div class="asset-empty">' + (kind === 'character' ? '还没有角色参考图' : '还没有场景参考图') + '</div>';
    });
    $('refCount').textContent = state.refs.length + ' 张';
    $('charRefCount').textContent = state.refs.filter(function (x) { return x.kind === 'character'; }).length;
    $('sceneRefCount').textContent = state.refs.filter(function (x) { return x.kind === 'scene'; }).length;
  }

  function renderVoices() {
    $('voiceCount').textContent = state.voices.length + ' 条';
    $('voiceList').innerHTML = state.voices.length ? state.voices.map(function (x) {
      var src = url(x);
      return '<div class="voice-item" data-id="' + esc(x.id) + '"><div class="voice-item-top"><b>' + esc(x.label || x.id) + '</b><button class="asset-del" title="删除语音">×</button></div>' +
        '<div class="voice-item-meta">' + esc(charName(x.character_id)) + ' · ' + secs(x.seconds) + ' · ' + bytes(x.bytes) + '</div>' +
        (src ? '<audio controls preload="metadata" src="' + esc(src) + '"></audio>' : '') +
        '<select class="voice-character" title="这是谁的声音">' + characterOptions(x.character_id) + '</select></div>';
    }).join('') : '<div class="asset-empty">上传语音后，可在这里试听并查看角色标注</div>';
  }

  async function uploadRefs(files) {
    clearError('refError');
    // 上传时就指定「这张图是谁」——场景参考不需要角色，所以只在 character 类时带上
    var refSel = $('refCharacter');
    var refCid = (state.kind === 'character' && refSel) ? (refSel.value || '') : '';
    for (var i = 0; i < files.length; i++) {
      var f = files[i]; var bad = validate(f, 'ref'); if (bad) { showError('refError', { message: bad }); continue; }
      try {
        var r = await GalAPI.uploadReference(pid(), f, state.kind, f.name.replace(/\.[^.]+$/, ''),
                                             undefined, undefined, refCid || undefined);
        if (r.reference && GalAPI.mode === 'mock') r.reference.asset = await readFile(f);
        state.refs.push(r.reference); renderRefs(); renderLibrary();
        toast('参考图上传成功：' + f.name + (refCid ? '（' + charName(refCid) + '）' : ''));
        // 底部「全部素材」扫的是磁盘，上传后必须重拉，否则 tab 数字变了、列表还是空的（踩过）
        await refreshLibraryOnly();
        if (refCid) await refreshRoles();
      } catch (e) { showError('refError', e); }
    }
    $('refFile').value = '';
  }

  async function uploadVoices(files) {
    clearError('voiceError');
    for (var i = 0; i < files.length; i++) {
      var f = files[i]; var bad = validate(f, 'voice'); if (bad) { showError('voiceError', { message: bad }); continue; }
      try {
        var character = $('voiceCharacter').value;
        var r = await GalAPI.uploadVoice(pid(), f, character, f.name.replace(/\.[^.]+$/, ''));
        if (r.voice && GalAPI.mode === 'mock') { r.voice.asset = await readFile(f); r.voice.seconds = await audioSeconds(f); }
        state.voices.push(r.voice); renderVoices(); toast('语音上传成功：' + f.name);
        await refreshLibraryOnly();   // 同上：底部素材库是磁盘扫描，必须重拉
      } catch (e) { showError('voiceError', e); }
    }
    $('voiceFile').value = '';
  }

  function bindDrop(zone, input, handler) {
    zone.addEventListener('click', function (e) { if (!e.target.closest('button,select')) input.click(); });
    zone.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
    input.addEventListener('change', function () { handler(Array.from(input.files || [])); });
    ['dragenter','dragover'].forEach(function (t) { zone.addEventListener(t, function (e) { e.preventDefault(); zone.classList.add('dragover'); }); });
    ['dragleave','drop'].forEach(function (t) { zone.addEventListener(t, function (e) { e.preventDefault(); zone.classList.remove('dragover'); }); });
    zone.addEventListener('drop', function (e) { handler(Array.from(e.dataTransfer.files || [])); });
  }

  function svgAsset(title, c1, c2) {
    var s = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="' + c1 + '"/><stop offset="1" stop-color="' + c2 + '"/></linearGradient></defs><rect width="640" height="360" fill="url(#g)"/><circle cx="510" cy="70" r="95" fill="#fff" opacity=".13"/><path d="M0 290 Q160 190 310 280 T640 230 V360 H0" fill="#fff" opacity=".18"/><text x="32" y="54" font-family="sans-serif" font-size="22" fill="#fff" opacity=".9">' + title + '</text></svg>';
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(s);
  }

  function demoData() {
    if (!state.refs.length) state.refs = [
      { id:'ref_demo_c', kind:'character', label:'女主 · 雨夜校服', bytes:842113, width:1024, height:1536, asset:svgAsset('CHARACTER / 01','#7771b8','#c5a6c8') },
      { id:'ref_demo_s', kind:'scene', label:'场景 · 冷色电影感', bytes:623118, width:1280, height:720, asset:svgAsset('SCENE / RAIN','#283451','#65738d') }
    ];
    if (!state.voices.length) state.voices = [{ id:'voi_demo', character_id:'heroine', label:'女主 · 你迟到了七分钟', seconds:2.64, bytes:126764, asset:'' }];
    state.library = { background:[{name:'雨夜天台.png',asset:svgAsset('雨夜天台','#27334e','#716886'),bytes:1824000},{name:'黄昏走廊.png',asset:svgAsset('黄昏走廊','#8b5966','#dfac81'),bytes:1542000}], character:[{name:'heroine_平静.png',asset:svgAsset(' heroine · 平静 ','#9b8fb4','#e2d6e5'),bytes:921000}], reference:[{name:'女主 · 雨夜校服',asset:state.refs[0].asset,bytes:842113}], voice:[{name:'女主 · 你迟到了七分钟',asset:'',seconds:2.64,bytes:126764}] };
  }

  async function loadAll() {
    clearError('assetGlobalError');
    $('assetProjectName').textContent = GalAPI.mode === 'mock' ? '雨停之前 · 演示项目' : pid();
    try {
      if (GalAPI.mode === 'real') {
        // 角色库：和 Agent 读的是同一份 graph.characters，顺带拿到「哪张立绘属于谁」
        var rc = await GalAPI.listCharacters(pid());
        state.chars = Array.isArray(rc.characters) ? rc.characters : [];
        state.assignments = rc.assignments || {};
        state.graphReady = rc.graph_ready !== false;
      } else if (!state.chars.length) {
        state.chars = [{id:'heroine',name:'她 · 女主',gender:'female',color:'#d946ef',counts:{poses:0,voices:0,nodes:0}},
                       {id:'hero',name:'我 · 男主',gender:'male',color:'#4f8ef7',counts:{poses:0,voices:0,nodes:0}}];
        state.assignments = {};
      }
      renderCharacters();
      var results = await Promise.all([GalAPI.listReferences(pid()), GalAPI.listVoices(pid()), GalAPI.getLibrary(pid())]);
      state.refs = results[0].references || []; state.voices = results[1].voices || []; state.library = results[2];
      state.libPicked = false;   // 换项目/重进素材页 → 允许自动挑一个非空分组
      if (qp.get('demo') === '1') demoData();
      renderRefs(); renderVoices(); renderLibrary();
      window.__galAssetsState = { references:state.refs.length, voices:state.voices.length, characters:state.chars.length };
    } catch (e) { showError('assetGlobalError', e); $('librarySummary').textContent = '读取失败'; }
  }

  function renderLibrary() {
    var lib = state.library || {background:[],character:[],reference:[],voice:[]};
    // 角色库不是磁盘文件，是项目里的角色表（graph.characters）——
    // 塞进 lib 里一起走下面那套逻辑，tab 数字 / 自动挑分组 / 网格渲染就只有一份代码。
    lib.roles = state.chars || [];
    // 只在「刚打开素材页、用户还没自己点过 tab」时，自动挑一个有内容的分组。
    // 以前是无条件跳 —— 于是点「背景 0」会跳到语音组，网格里出现 2 张语音卡，
    // tab 上却还是 0，看的人以为数字坏了（主人 2026-11-08 第二次抓到的就是这个）。
    // 用户自己点过之后就不再插手：点哪个分组就显示哪个，空就是空。
    if (!state.libPicked && !(lib[state.lib] || []).length) {
      var order = ['roles', 'voice', 'reference', 'character', 'background'];
      for (var oi = 0; oi < order.length; oi++) {
        if ((lib[order[oi]] || []).length) { state.lib = order[oi]; break; }
      }
    }
    // tab 上的数字必须和网格里的卡片数一模一样。
    // 之前语音/参考图这两个 tab 显示的是「登记条数」，网格却是磁盘扫描结果 ——
    // 于是出现「语音 0，下面却有一张卡」（主人 2026-11-08 抓到的）。
    // 「全部素材」这一块本身就是磁盘视图，全部按磁盘算，口径统一。
    document.querySelectorAll('#libraryTabs button').forEach(function (b) {
      var k = b.dataset.lib;
      b.querySelector('span').textContent = (lib[k] || []).length;
      b.classList.toggle('on', k === state.lib);
    });
    var arr = lib[state.lib] || [];
    var regN = { voice: (state.voices || []).length, reference: (state.refs || []).length };
    if (state.lib === 'roles') {
      $('librarySummary').textContent = arr.length
        ? arr.length + ' 个角色 · 立绘和语音只能挂到这里建的角色上'
        : '还没有角色 · 先建一个，立绘和语音才能挂上去';
    } else if (state.lib === 'voice') {
      $('librarySummary').textContent = arr.length + ' 个音频文件 · 其中 ' + regN.voice + ' 条已登记语音';
    } else if (state.lib === 'reference') {
      $('librarySummary').textContent = arr.length + ' 个文件 · 其中 ' + regN.reference + ' 张已登记参考图';
    } else {
      $('librarySummary').textContent = arr.length + ' 项当前分组 · 磁盘实时数据';
    }
    var newRoleCard = '<div class="role-new" data-new-role="1">' +
        '<input class="role-new-name" maxlength="20" placeholder="新角色的名字" aria-label="新角色的名字">' +
        '<div class="role-new-row"><select class="role-new-gender" aria-label="性别">' +
          '<option value="female">女</option><option value="male">男</option><option value="other">其他</option>' +
        '</select><button class="role-new-btn" type="button">新建角色</button></div></div>';
    $('libraryGrid').innerHTML = state.lib === 'roles' ? newRoleCard + arr.map(function (x) {
      var letter = String(x.name || '?').trim().slice(0, 1) || '?';
      var c = x.counts || {};
      var gender = x.gender || 'other';
      return '<div class="role-card" data-cid="'+esc(x.id)+'">' +
        '<div class="role-avatar" style="background:'+esc(x.color || '#8b6bd6')+'">'+esc(letter)+'</div>' +
        '<div class="role-main">' +
          '<input class="role-name" data-cid="'+esc(x.id)+'" maxlength="20" value="'+esc(x.name || '')+'" title="点这里改名字" placeholder="角色名">' +
          '<div class="role-sub">' +
            '<select class="role-gender" data-cid="'+esc(x.id)+'" title="性别（Agent 写台词时靠它分他/她）">' +
              [['female','女'],['male','男'],['other','其他']].map(function (p) {
                return '<option value="'+p[0]+'"'+(gender === p[0] ? ' selected' : '')+'>'+p[1]+'</option>';
              }).join('') +
            '</select>' +
            '<span class="role-count" title="立绘 / 语音 / 剧情节点 各有多少用着这个角色">立绘'+(c.poses||0)+' 语音'+(c.voices||0)+' 剧情'+(c.nodes||0)+'</span>' +
          '</div>' +
        '</div>' +
        '<button class="lib-del role-del" data-cid="'+esc(x.id)+'" title="删除这个角色" aria-label="删除这个角色">' +
          '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16"/><path d="M9.5 7V5.4A1.4 1.4 0 0 1 10.9 4h2.2a1.4 1.4 0 0 1 1.4 1.4V7"/><path d="M6.6 7l.78 12.06A2 2 0 0 0 9.38 21h5.24a2 2 0 0 0 2-1.94L17.4 7"/><path d="M10.2 11v6M13.8 11v6"/></svg>' +
        '</button></div>';
    }).join('') : (arr.length ? arr.map(function (x) {
      var src=url(x), name=x.name || (x.asset||'').split('/').pop();
      if (state.lib === 'voice') {
        // 「全部素材」里的语音是扫磁盘出来的，以前只有一个播放器 —— 删不掉、也不能标角色。
        // 这里按 asset 路径对回 voices.json 的标注，没登记的就显示「未标注」。
        var vrec = (state.voices || []).filter(function (v) { return v.asset === x.asset; })[0];
        return '<div class="library-card" data-asset="'+esc(x.asset)+'"><div class="library-audio"><div class="audio-glyph">♫</div>' +
          (src ? '<audio controls preload="metadata" src="'+esc(src)+'"></audio>' : '<div class="asset-empty">模拟语音暂无音频文件</div>') +
          '</div><div class="library-info"><b>'+esc(name)+'</b><span>'+bytes(x.bytes)+(x.seconds?' · '+secs(x.seconds):'')+'</span></div>' +
          '<div class="lib-voice-edit"><select class="lib-voice-char" title="这是谁的声音">'+characterOptions(vrec ? vrec.character_id : '')+'</select>' +
          '<button class="lib-del" data-asset="'+esc(x.asset)+'" title="删除这个文件" aria-label="删除这个文件"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16"/><path d="M9.5 7V5.4A1.4 1.4 0 0 1 10.9 4h2.2a1.4 1.4 0 0 1 1.4 1.4V7"/><path d="M6.6 7l.78 12.06A2 2 0 0 0 9.38 21h5.24a2 2 0 0 0 2-1.94L17.4 7"/><path d="M10.2 11v6M13.8 11v6"/></svg></button></div></div>';
      }
      if (state.lib === 'character') {
        // 立绘：哪张图是哪个角色，存在 characters_map.json 里（立绘本身没有登记表）
        var owner = (state.assignments || {})[x.asset] || '';
        return '<div class="library-card" data-asset="'+esc(x.asset)+'">' +
          (src?'<img class="library-preview" src="'+esc(src)+'" alt="'+esc(name)+'">':'<div class="library-preview"></div>') +
          '<div class="library-info"><b>'+esc(name)+'</b><span>'+esc(x.asset||'')+' · '+bytes(x.bytes)+'</span></div>' +
          '<div class="lib-voice-edit"><select class="lib-pose-char" data-asset="'+esc(x.asset)+'" title="这张立绘是谁">'+characterOptions(owner)+'</select></div></div>';
      }
      if (state.lib === 'reference') {
        // 磁盘项按 asset 路径对回登记记录 —— 只有登记过的才能挂角色
        var reg = (state.refs || []).filter(function (r) { return r.asset === x.asset; })[0];
        var edit;
        if (!reg) {
          edit = '<div class="lib-ref-edit"><span class="lib-ref-orphan">没有登记，只能看不能用</span></div>';
        } else if (reg.kind === 'scene') {
          // 场景参考图不是人物，给它起个场景名还是有意义的
          edit = '<div class="lib-ref-edit">' +
              '<input class="lib-ref-name" data-ref-id="'+esc(reg.id)+'" maxlength="20" placeholder="给这个场景起个名字" value="'+esc(reg.name || '')+'">' +
            '</div>';
        } else {
          // 角色参考图：从角色库里选「这是谁的参考」——
          // 以前给一个输入框让人手打名字，打出来的名字和角色库对不上，
          // 生成时会被当成另一个角色（主人 2026-11-08 要求改成选人物）。
          edit = '<div class="lib-ref-edit">' +
              '<select class="lib-ref-char" data-ref-id="'+esc(reg.id)+'" title="这张参考图是谁">' +
                characterOptions(reg.character_id || '') +
              '</select>' +
            '</div>';
        }
        return '<div class="library-card">'+(src?'<img class="library-preview" src="'+esc(src)+'" alt="'+esc(name)+'">':'<div class="library-preview"></div>')+'<div class="library-info"><b>'+esc(reg && reg.name ? reg.name : name)+'</b><span>'+esc(x.asset||'')+' · '+bytes(x.bytes)+'</span></div>'+edit+'</div>';
      }
      return '<div class="library-card">'+(src?'<img class="library-preview" src="'+esc(src)+'" alt="'+esc(name)+'">':'<div class="library-preview"></div>')+'<div class="library-info"><b>'+esc(name)+'</b><span>'+esc(x.asset||'')+' · '+bytes(x.bytes)+'</span></div></div>';
    }).join('') : '<div class="asset-empty">这个分组还是空的</div>');
  }

  // 在素材库卡片上给参考图改名 / 选性别：上传时往往还没想好叫什么，
  // 事后对着图改才顺手（主人 2026-11-08 定）。
  $('libraryGrid').addEventListener('change', async function (e) {
    var el = e.target;

    // 角色库：改角色名字（失焦 / 回车才提交，不用每敲一个字就请求）
    if (el.classList.contains('role-name')) {
      var rcid = el.dataset.cid;
      var want = (el.value || '').trim();
      var current = (state.chars.filter(function (c) { return c.id === rcid; })[0] || {});
      if (!want) { el.value = current.name || ''; toast('角色名不能为空'); return; }
      if (want === current.name) return;
      try {
        var ur = await GalAPI.updateCharacter(pid(), rcid, { name: want });
        if (ur.character) Object.assign(current, ur.character);
        renderLibrary(); renderCharacters();
        toast('已改名：' + want);
      } catch (err) {
        el.value = current.name || '';
        showError('assetGlobalError', err);
      }
      return;
    }

    // 角色库：改性别
    if (el.classList.contains('role-gender')) {
      var gcid = el.dataset.cid;
      var row = (state.chars.filter(function (c) { return c.id === gcid; })[0] || {});
      try {
        var gr = await GalAPI.updateCharacter(pid(), gcid, { gender: el.value });
        if (gr.character) Object.assign(row, gr.character);
        toast('已保存性别');
      } catch (err) { showError('assetGlobalError', err); }
      return;
    }

    // 立绘挂角色（存 characters_map.json）
    if (el.classList.contains('lib-pose-char')) {
      var passet = el.dataset.asset;
      if (!passet) return;
      try {
        await GalAPI.assignCharacter(pid(), passet, el.value);
        state.assignments = state.assignments || {};
        if (el.value) state.assignments[passet] = el.value; else delete state.assignments[passet];
        toast('这张立绘属于：' + (el.value ? charName(el.value) : '未指定'));
        await refreshRoles();
      } catch (err) { showError('assetGlobalError', err); }
      return;
    }

    // 「全部素材」里的语音标注：按 asset 路径存，没登记的后端会补一条
    if (el.classList.contains('lib-voice-char')) {
      var vcard = el.closest('.library-card');
      var vasset = vcard ? vcard.dataset.asset : '';
      if (!vasset) return;
      try {
        var vr = await GalAPI.assignCharacter(pid(), vasset, el.value);
        if (state.voices) {
          var hit = state.voices.filter(function (v) { return v.asset === vasset; })[0];
          if (hit) hit.character_id = el.value; else if (vr.voice) state.voices.push(vr.voice);
        }
          toast('已标注：' + (el.value ? charName(el.value) : '未指定'));
          await refreshRoles();
      } catch (err) { showError('assetGlobalError', err); }
      return;
    }
    // 参考图挂角色：从角色库里选一个人，不再手打名字
    if (el.classList.contains('lib-ref-char')) {
      var choseRef = el.dataset.refId;
      if (!choseRef) return;
      try {
        // 只传 character_id：名字和性别由后端按角色补齐，不传空串（那会把名字清空）
        var picked = await GalAPI.renameReference(pid(), choseRef, undefined, undefined, el.value);
        var rec = (state.refs || []).filter(function (r) { return r.id === choseRef; })[0];
        if (rec && picked.reference) {
          rec.character_id = picked.reference.character_id;
          rec.name = picked.reference.name;
          rec.gender = picked.reference.gender;
        }
        var ttl = el.closest('.library-card') && el.closest('.library-card').querySelector('.library-info b');
        if (ttl) ttl.textContent = (picked.reference && picked.reference.name) || (rec && rec.label) || '未命名';
        renderRefs();
        toast(el.value ? '这张参考图属于：' + charName(el.value) : '这张参考图不指定角色');
      } catch (err) { showError('assetGlobalError', err); }
      return;
    }
    if (!el.classList.contains('lib-ref-name') && !el.classList.contains('lib-ref-gender')) return;
    var card = el.closest('.lib-ref-edit');
    if (!card) return;
    var id = el.dataset.refId;
    var nameEl = card.querySelector('.lib-ref-name');
    var genderEl = card.querySelector('.lib-ref-gender');
    var name = (nameEl.value || '').trim();
    try {
      var res = await GalAPI.renameReference(pid(), id, name, genderEl.value);
      var reg = (state.refs || []).filter(function (r) { return r.id === id; })[0];
      if (reg && res.reference) { reg.name = res.reference.name; reg.gender = res.reference.gender; }
      var title = card.parentNode.querySelector('.library-info b');
      if (title && name) title.textContent = name;
      renderRefs();
      toast('已保存：' + (name || '未命名'));
    } catch (err) { showError('assetGlobalError', err); }
  });

  function preview(src, alt) {
    var mask=document.createElement('div'); mask.className='asset-preview-mask'; mask.innerHTML='<div class="asset-preview-box"><button aria-label="关闭">×</button><img src="'+esc(src)+'" alt="'+esc(alt)+'"></div>';
    mask.addEventListener('click',function(e){if(e.target===mask||e.target.tagName==='BUTTON')mask.remove();}); document.body.appendChild(mask);
  }

  function bindAssets() {
    document.querySelectorAll('[data-ref-kind]').forEach(function(b){b.addEventListener('click',function(e){e.stopPropagation();state.kind=b.dataset.refKind;document.querySelectorAll('[data-ref-kind]').forEach(function(x){x.classList.toggle('on',x===b);});syncRefCharPicker();});});
    syncRefCharPicker();
    // 下拉在拖拽区里，点它会连带触发「打开文件选择」，必须拦掉（和语音那边一样）
    if ($('refCharacter')) $('refCharacter').addEventListener('click', function (e) { e.stopPropagation(); });
    bindDrop($('refDrop'),$('refFile'),uploadRefs); bindDrop($('voiceDrop'),$('voiceFile'),uploadVoices);
    $('voiceCharacter').addEventListener('click',function(e){e.stopPropagation();});
    $('btnRefreshAssets').addEventListener('click',loadAll);
    document.querySelectorAll('[data-view="assets"]').forEach(function(b){b.addEventListener('click',function(){setTimeout(loadAll,0);});});
    $('refCount').parentElement.parentElement.addEventListener('click',async function(e){
      var item=e.target.closest('.ref-item'); if(!item)return;
      if(e.target.classList.contains('ref-thumb')){preview(e.target.src,e.target.alt);return;}
      if(!e.target.classList.contains('asset-del'))return;
      e.target.disabled=true; clearError('refError');
      try{await GalAPI.deleteReference(pid(),item.dataset.id);state.refs=state.refs.filter(function(x){return x.id!==item.dataset.id;});renderRefs();await refreshLibraryOnly();toast('参考图已删除');}catch(err){showError('refError',err);e.target.disabled=false;}
    });
    $('voiceList').addEventListener('click',async function(e){var item=e.target.closest('.voice-item');if(!item||!e.target.classList.contains('asset-del'))return;e.target.disabled=true;clearError('voiceError');try{await GalAPI.deleteVoice(pid(),item.dataset.id);state.voices=state.voices.filter(function(x){return x.id!==item.dataset.id;});renderVoices();await refreshLibraryOnly();toast('语音已删除');}catch(err){showError('voiceError',err);e.target.disabled=false;}});
    // 标注「这条语音是谁的声音」—— 必须真的存到后端，生成 Agent 才读得到
    $('voiceList').addEventListener('change',async function(e){if(!e.target.classList.contains('voice-character'))return;var item=e.target.closest('.voice-item');if(!item)return;var v=state.voices.filter(function(x){return x.id===item.dataset.id;})[0];if(!v)return;var picked=e.target.value;v.character_id=picked;clearError('voiceError');try{await GalAPI.updateVoice(pid(),item.dataset.id,{character_id:picked});toast('已标注：'+(picked?charName(picked):'未指定'));}catch(err){showError('voiceError',err);}});
    $('libraryTabs').addEventListener('click',function(e){var b=e.target.closest('button[data-lib]');if(b){state.lib=b.dataset.lib;state.libPicked=true;renderLibrary();}});
    $('libraryGrid').addEventListener('click',async function(e){
    // 角色库：删角色。第一次点只是「上膛」，第二次才真删 —— 免得手滑。
    var roleDel = e.target.closest('.role-del');
    if(roleDel){
      var rcid = roleDel.dataset.cid;
      if(!rcid) return;
      var row = state.chars.filter(function(c){return c.id===rcid;})[0] || {};
      var cnt = row.counts || {};
      var total = (cnt.poses||0) + (cnt.voices||0) + (cnt.nodes||0);
      var nm = row.name || '这个角色';
      // 一次点击就弹确认框（以前是「先上膛再点一次」，多一步还容易看不出状态）
      var msg = total
        ? '「' + nm + '」还挂着 ' + (cnt.poses||0) + ' 张立绘、' + (cnt.voices||0)
          + ' 条语音、' + (cnt.nodes||0) + ' 个剧情节点。\n删除会把这些地方的角色标注一并清空（立绘和语音文件本身不会删）。'
        : '确定删除角色「' + nm + '」？';
      // 删角色一律用红色按钮 —— 是不是危险操作，跟有没有引用无关
      if(!(await askConfirm(msg, '删除', true))) return;
      roleDel.disabled = true;
      try{
        await GalAPI.deleteCharacter(pid(), rcid, total > 0);
        state.chars = state.chars.filter(function(x){return x.id!==rcid;});
        state.assignments = state.assignments || {};
        Object.keys(state.assignments).forEach(function(k){ if(state.assignments[k]===rcid) delete state.assignments[k]; });
        // 挂在这个角色上的语音、立绘都要重拉 —— 否则上面「已登记语音」还写着旧角色的 id
        await loadAll();
        toast(total > 0 ? '角色和它的挂载都已清空' : '角色已删除');
      }catch(err){ roleDel.disabled = false; showError('assetGlobalError', err); }
      return;
    }

    // 角色库：新建角色
    if(e.target.closest('.role-new-btn')){
      var box = e.target.closest('.role-new');
      if(!box) return;
      var nameEl = box.querySelector('.role-new-name');
      var genderEl = box.querySelector('.role-new-gender');
      var name = (nameEl.value||'').trim();
      if(!name){ toast('先给角色起个名字'); nameEl.focus(); return; }
      var btn = box.querySelector('.role-new-btn');
      btn.disabled = true;
      try{
        var made = await GalAPI.createCharacter(pid(), { name: name, gender: genderEl.value });
        if(made.character) state.chars = (state.chars||[]).concat([made.character]);
        renderCharacters(); renderLibrary();
        toast('已新建角色：' + name);
      }catch(err){ btn.disabled = false; showError('assetGlobalError', err); }
      return;
    }

    // 「全部素材」里的删除：真删磁盘文件（以前这些卡片根本没有删除入口）
    // 按钮里是 SVG 图标，点中的是 path 而不是 button，所以只能用 closest 往上找。
    var delBtn = e.target.closest('.lib-del');
    if(delBtn){
      var dasset=delBtn.dataset.asset; if(!dasset)return;
      delBtn.disabled=true;
      try{await GalAPI.deleteLibraryItem(pid(),dasset);await refreshLibraryOnly();
        state.voices=(state.voices||[]).filter(function(v){return v.asset!==dasset;});renderVoices();
        toast('已删除');}catch(err){showError('assetGlobalError',err);delBtn.disabled=false;}
      return;
    }
    if(e.target.classList.contains('library-preview'))preview(e.target.src,e.target.alt);
  });
  }

  async function refreshLibraryOnly(){try{state.library=await GalAPI.getLibrary(pid());renderLibrary();}catch(e){showError('assetGlobalError',e);}}

  // 重新拉角色库：立绘/语音挂到谁身上之后，角色卡片上的计数要跟着变
  async function refreshRoles(){
    try{
      var rc = await GalAPI.listCharacters(pid());
      state.chars = Array.isArray(rc.characters) ? rc.characters : state.chars;
      state.assignments = rc.assignments || state.assignments;
      state.graphReady = rc.graph_ready !== false;
      renderCharacters();
      if(state.lib === 'roles') renderLibrary();
    }catch(e){ /* 计数没刷上不该打断操作 */ }
  }

  function unityProgress(percent, text) {
    $('unityProgress').hidden=false; $('unityPercent').textContent=Math.round(percent)+'%'; $('unityBar').style.width=Math.max(3,percent)+'%'; if(text)$('unityStage').textContent=text;
  }

  async function exportUnity() {
    if(state.busy)return; state.busy=true; clearError('unityError'); var b=$('btnExportUnity');b.disabled=true;b.textContent='Unity 构建中…';unityProgress(4,'正在准备工程，通常需要 3–10 分钟');
    try{
      if(GalAPI.mode==='real'&&!pid())throw new Error('还没有绑定项目，请先打开或新建项目');
      var r=await GalAPI.exportProject(pid(),'unity');
      if(!r||!r.ok)throw new Error((r&&r.detail)||'导出请求失败');
      if(r.job_id&&GalAPI.mode==='real'){
        unityProgress(10,'Unity 正在构建，别关窗口');
        var done=await GalAPI.watchJob(r.job_id,function(type,ev){
          if(type==='agent'||type==='job'){var p=typeof ev.progress==='number'?(ev.progress<=1?ev.progress*100:ev.progress):18;unityProgress(Math.max(10,Math.min(98,p)),ev.msg||ev.message||'Unity 正在构建，别关窗口');}
          if(type==='log')$('unityStage').textContent=ev.text||'正在构建';
        });
        if(!done||!done.ok)throw new Error((done&&done.error)||'Unity 构建中断');
      }else if(GalAPI.mode==='mock'){
        for(var p=12;p<=92;p+=8){unityProgress(p,p<40?'生成 Unity 工程与资源索引':p<75?'编译脚本、打包场景':'写出 Windows 可执行文件');await new Promise(function(res){setTimeout(res,180);});}
      }
      unityProgress(100,'构建完成：'+(r.out||'export/unity/'));b.textContent='再次导出 Unity';toast('Unity 桌面版构建完成');
    }catch(e){showError('unityError',e);$('unityStage').textContent='构建失败，请查看下方具体原因';b.textContent='重试 Unity 导出';}finally{state.busy=false;b.disabled=false;}
  }

  bindAssets(); $('btnExportUnity').addEventListener('click',exportUnity);
  if(qp.get('demo')==='1')demoData();
  renderRefs();renderVoices();renderCharacters();

  // 素材库属于某个具体项目：拿不到项目就明确说，不要退到 'demo' 假装在看数据。
  function setPanelVisible(visible) {
    ['assetNoProject'].forEach(function () {});   // 占位，保持结构对称
    ['libraryTabs', 'libraryGrid'].forEach(function (id) {
      var el = $(id); if (el) el.hidden = !visible;
    });
    document.querySelectorAll('.library-head').forEach(function (el) { el.hidden = !visible; });
    var wb = document.querySelector('.asset-workbench');
    if (wb) wb.hidden = !visible;
    var hero = document.querySelector('.assets-hero');
    if (hero) hero.hidden = !visible;
    var crumb = document.querySelector('.assets-crumb');
    if (crumb) crumb.hidden = false;               // 面包屑/返回始终可见
  }
  function hasProject() { return !!pid(); }
  function showNoProject() {
    var box = $('assetNoProject');
    if (box) box.hidden = false;
    setPanelVisible(false);
  }

  // 从「剧情编排」点进素材库时由 app.js 调这个：按当前项目重载
  window.__galAssetsEnter = function () {
    var box = $('assetNoProject');
    if (!hasProject()) { if (box) box.hidden = false; setPanelVisible(false); return; }
    if (box) box.hidden = true;
    setPanelVisible(true);
    loadAll();
  };

  if (!hasProject()) { showNoProject(); }
  else if (qp.get('view') === 'assets' || qp.get('demo') === '1') setTimeout(loadAll, 80);
  if(qp.get('unitytest')==='1')setTimeout(exportUnity,300);
  window.GalAssetsTest={uploadRefs:uploadRefs,uploadVoices:uploadVoices,load:loadAll,state:function(){return{refs:state.refs.length,voices:state.voices.length,kind:state.kind,lib:state.lib};}};

})();
