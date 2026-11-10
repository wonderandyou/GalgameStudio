/* ============================================================
   奇迹工坊 · 接口层
   作用：把「模拟数据」和「真后端」收进同一个出口。
   - mock 模式：本地按事件脚本播动画（现在的默认）
   - real 模式：走 FastAPI（127.0.0.1:8790）+ SSE
   上层 app.js 只认 runGeneration(cb) 这一套事件，不关心数据从哪来。
   ============================================================ */
(function (global) {
  'use strict';

  const DEFAULT_BASE = 'http://127.0.0.1:8790';

  /* ---------- mock：跟后端 SSE 完全一致的事件脚本 ---------- */
  const MOCK_SCRIPT = [
    { d: 120, t: 'agent', agent: 'writer', state: 'run', progress: 0.05, msg: '解析需求…' },
    { d: 380, t: 'log', level: 'info', text: '解析需求：校园 / 悬疑 / 双分支 / 留白' },
    { d: 520, t: 'log', level: 'info', text: '生成大纲：天台 → 车票 → 分岔 → 三结局' },
    { d: 700, t: 'agent', agent: 'writer', state: 'run', progress: 0.55, msg: '写第 5/9 段台词' },
    { d: 900, t: 'log', level: 'info', text: '台词 9 段完成，平均 26 字，最长 41 字' },
    { d: 260, t: 'agent', agent: 'writer', state: 'done', progress: 1, msg: '9 段台词' },

    { d: 200, t: 'agent', agent: 'art', state: 'run', progress: 0.08, msg: '提取场景表…' },
    { d: 620, t: 'log', level: 'info', text: '场景去重：9 个节点 → 3 张背景（雨夜天台 / 楼梯间 / 站台）' },
    { d: 700, t: 'log', level: 'info', text: '角色一致性：锁定 heroine 参考图，走 img2img 通道' },
    { d: 900, t: 'agent', agent: 'art', state: 'run', progress: 0.42, msg: '出背景 1/3' },
    { d: 700, t: 'agent', agent: 'art', state: 'run', progress: 0.68, msg: '出立绘 2/4' },
    { d: 600, t: 'log', level: 'warn', text: '1 张立绘手部崩坏 → 重绘 1 次（seed +1）' },
    { d: 500, t: 'agent', agent: 'art', state: 'done', progress: 1, msg: '12 张完成' },

    { d: 300, t: 'agent', agent: 'voice', state: 'run', progress: 0.2, msg: '切句 9 条 → longxiaochun' },
    { d: 800, t: 'log', level: 'info', text: '本地 TTS 未启动 → 回落百炼云端（记 1 条 warn）' },
    { d: 700, t: 'agent', agent: 'voice', state: 'run', progress: 0.7, msg: '合成 7/9 条' },
    { d: 500, t: 'log', level: 'info', text: '静音裁剪 + 响度归一化 -16 LUFS，平均 4.4s' },
    { d: 200, t: 'agent', agent: 'voice', state: 'done', progress: 1, msg: '9 条语音' },

    { d: 260, t: 'agent', agent: 'build', state: 'run', progress: 0.3, msg: '回写节点…' },
    { d: 480, t: 'log', level: 'info', text: '回写 9 节点 / 8 连线，自动排布完成' },
    { d: 480, t: 'log', level: 'info', text: '图校验：死链 0 / 环 0 / 分支全收敛 ✓' },
    { d: 240, t: 'agent', agent: 'build', state: 'done', progress: 1, msg: '校验通过' }
  ];

  const GalAPI = {
    mode: 'mock',              // 'mock' | 'real'
    base: DEFAULT_BASE,
    online: false,
    lastError: '',

    /* ---------------- 基础 ---------------- */
    setMode(m) {
      this.mode = m === 'real' ? 'real' : 'mock';
      localStorage.setItem('gal_mode', this.mode);
      return this.mode;
    },
    setBase(url) {
      this.base = (url || DEFAULT_BASE).replace(/\/+$/, '');
      localStorage.setItem('gal_base', this.base);
      return this.base;
    },

    /** 探测后端是否在线；2 秒超时 */
    async detect() {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 2000);
      try {
        const r = await fetch(this.base + '/api/health', { signal: ctl.signal });
        clearTimeout(timer);
        if (!r.ok) { this.online = false; return null; }
        const j = await r.json();
        this.online = !!j.ok;
        return j;
      } catch (e) {
        clearTimeout(timer);
        this.online = false;
        this.lastError = e && e.name === 'AbortError' ? '连接超时（2s）' : String(e && e.message || e);
        return null;
      }
    },

    /* ---------------- provider ---------------- */
    async getProviders() {
      if (this.mode === 'mock') {
        try { return JSON.parse(localStorage.getItem('gal_providers') || 'null'); }
        catch (e) { return null; }
      }
      try {
        const r = await fetch(this.base + '/api/providers');
        if (!r.ok) return null;
        return await r.json();
      } catch (e) { return null; }
    },

    async saveProviders(cfg) {
      if (this.mode === 'mock') {
        localStorage.setItem('gal_providers', JSON.stringify(cfg));
        return { ok: true, saved: Object.keys(cfg).length };
      }
      try {
        const r = await fetch(this.base + '/api/providers', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(cfg)
        });
        if (!r.ok) return { ok: false, detail: 'HTTP ' + r.status };
        return await r.json();
      } catch (e) {
        return { ok: false, detail: String(e.message || e) };
      }
    },

    async testProvider(kind) {
      if (this.mode === 'mock') {
        await sleep(600 + Math.random() * 400);
        const dead = kind === 'local_tts' || kind === 'local_llm';
        return dead
          ? { ok: false, detail: '连接被拒绝 · 请先启动服务' }
          : { ok: true, latency_ms: Math.round(200 + Math.random() * 600) };
      }
      try {
        const r = await fetch(this.base + '/api/providers/' + kind + '/test', { method: 'POST' });
        return await r.json();
      } catch (e) {
        return { ok: false, detail: '请求失败：' + (e.message || e) };
      }
    },

    /* ---------------- 项目 ---------------- */
    /** real 模式下确保有项目；mock 直接返回假 id */
    async ensureProject(name) {
      if (this.mode === 'mock') return { id: 'demo', name: name || '雨停之前' };
      const r = await fetch(this.base + '/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name || '雨停之前' })
      });
      if (!r.ok) throw new Error('创建项目失败 HTTP ' + r.status);
      return await r.json();
    },

    /**
     * 列出后端所有项目（按最近更新时间倒序）。
     * 后端已经把节点数 / 结局数 / 是否有导出算好了，前端不用再逐个拉 graph。
     * mock 模式返回一个假项目，让界面不至于空着。
     */
    async listProjects() {
      if (this.mode === 'mock') {
        return [{
          id: 'demo', name: '雨停之前（模拟）', resolution: [1920, 1080],
          created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
          node_count: 9, ending_count: 3, choice_count: 2, has_export: false
        }];
      }
      const r = await fetch(this.base + '/api/projects');
      if (!r.ok) throw new Error('读取项目列表失败 HTTP ' + r.status);
      const arr = await r.json();
      return Array.isArray(arr) ? arr : [];
    },

    /** 删项目（连目录一起删）。后端会拒绝删正在生成的项目，把原因抛出来 */
    async deleteProject(projectId) {
      if (this.mode === 'mock') {
        await sleep(200);
        return { ok: true, mock: true };
      }
      const r = await fetch(this.base + '/api/projects/' + encodeURIComponent(projectId), {
        method: 'DELETE'
      });
      if (!r.ok) {
        let detail = 'HTTP ' + r.status;
        try {
          const j = await r.json();
          if (j && j.error && j.error.message) detail = j.error.message;
        } catch (e) { /* 响应不是 JSON 就沿用状态码 */ }
        throw new Error(detail);
      }
      return await r.json();
    },

    async getGraph(projectId) {
      if (this.mode === 'mock') return null;
      const r = await fetch(this.base + '/api/projects/' + projectId + '/graph');
      if (!r.ok) throw new Error('读取项目图失败 HTTP ' + r.status);
      return await r.json();
    },

    /** 续写状态：上次写到哪、盘上的图对不对得上检查点。打不开就返回 null */
    async getResume(projectId) {
      if (this.mode === 'mock') return null;
      try {
        const r = await fetch(this.base + '/api/projects/' + projectId + '/resume');
        return r.ok ? await r.json() : null;
      } catch (e) {
        return null;
      }
    },

    /** 保存画布（只存不生成）。graph 结构见契约 §3.6 */
    async saveGraph(projectId, graph) {
      if (this.mode === 'mock') {
        await sleep(240);
        return { ok: true, mock: true };
      }
      try {
        const r = await fetch(this.base + '/api/projects/' + projectId + '/graph', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(graph)
        });
        const body = await r.json().catch(function () { return {}; });
        if (!r.ok) {
          const msg = (body && body.error && body.error.message) || ('HTTP ' + r.status);
          return { ok: false, detail: msg, status: r.status };
        }
        return { ok: true, raw: body };
      } catch (e) {
        return { ok: false, detail: String((e && e.message) || e) };
      }
    },

    /** 导出：format = renpy | web | unity | video */
    async exportProject(projectId, format) {
      if (this.mode === 'mock') {
        await sleep(900 + Math.random() * 700);
        return { ok: true, out: 'D:\\GalgameStudio\\out\\雨停之前\\export\\' + format, mock: true,
          job_id: format === 'unity' ? 'mock_unity_' + Date.now() : undefined };
      }
      try {
        const r = await fetch(this.base + '/api/projects/' + projectId + '/export', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ format: format })
        });
        const body = await r.json().catch(function () { return {}; });
        if (!r.ok) {
          // 后端把原因放在 error.message 里（例如「项目已有运行任务」），
          // 只回 HTTP 409 等于把用户当瞎子。
          const e = body && body.error;
          return {
            ok: false,
            detail: (e && e.message) || ('HTTP ' + r.status),
            code: (e && e.code) || '',
            status: r.status
          };
        }
        return body;
      } catch (e) {
        return { ok: false, detail: String((e && e.message) || e) };
      }
    },

    /**
     * 按文字要求 + 参考图设计界面风格。
     * 返回 { ok, preset:{id,name,vibe,palette}, screens:[{label,url,path}], analysis }；
     * 失败统一回 { ok:false, error:{message} }，前端直接显示 error.message。
     */
    async designUi(projectId, payload) {
      if (this.mode === 'mock') return { ok: true, mock: true, preset: {id:'designed-mock', name:'纸感极简', vibe:'模拟模式不会真的设计界面', base:'paper-indie', palette:{bg:'#f4efe4',fg:'#23282a',accent:'#b8443a',muted:'#6a6153',panel:'#efe8d8'}}, screens: [], analysis:{colors:[],matched:[],keywords:[]} };
      const r = await fetch(this.base + '/api/projects/' + encodeURIComponent(projectId) + '/ui/design', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      const body = await r.json().catch(function(){ return {}; });
      if (!r.ok) return { ok:false, error: (body.error || {message:'HTTP ' + r.status}) };
      return body;
    },

    /* ---------------- 生成 ---------------- */
    /**
     * 跑一次生成。
     * @param {{projectId:string, requirement:string, params:object}} req
     * @param {(type:string, data:object)=>void} onEvent  事件回调
     * @returns {Promise<{ok:boolean, stats?:object, error?:string}>}
     */
    async runGeneration(req, onEvent) {
      if (this.mode === 'mock') return this._mockRun(onEvent);
      return this._realRun(req, onEvent);
    },

    _mockRun(onEvent) {
      return new Promise((resolve) => {
        let i = 0;
        const tick = () => {
          if (i >= MOCK_SCRIPT.length) {
            onEvent('done', {
              stats: { nodes: 9, edges: 8, images: 12, voices: 9, cost: 0, elapsed_s: 42 }
            });
            resolve({ ok: true });
            return;
          }
          const ev = MOCK_SCRIPT[i++];
          const { d, ...payload } = ev;
          setTimeout(() => {
            onEvent(payload.t, payload);
            tick();
          }, d);
        };
        tick();
      });
    },

    async _realRun(req, onEvent) {
      let jobId;
      try {
        const r = await fetch(this.base + '/api/projects/' + req.projectId + '/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ requirement: req.requirement, params: req.params })
        });
        if (!r.ok) {
          const b = await r.json().catch(function () { return {}; });
          const e = b && b.error;
          throw new Error((e && e.message) || ('HTTP ' + r.status));
        }
        const j = await r.json();
        jobId = j.job_id;
        onEvent('jobstart', { job_id: jobId });
      } catch (e) {
        onEvent('error', { code: 'START_FAILED', message: String(e.message || e) });
        return { ok: false, error: String(e.message || e) };
      }

      return new Promise((resolve) => {
        const url = this.base + '/api/jobs/' + jobId + '/events';
        let es;
        try {
          es = new EventSource(url);
        } catch (e) {
          onEvent('error', { code: 'SSE_FAILED', message: String(e.message || e) });
          resolve({ ok: false, error: String(e.message || e) });
          return;
        }

        const close = () => { try { es.close(); } catch (e) {} };
        const on = (name) => es.addEventListener(name, (ev) => {
          let data = {};
          try { data = JSON.parse(ev.data); } catch (e) {}
          if (name === 'done' || (name === 'job' && data.state === 'done')) { onEvent('done', data); close(); resolve({ ok: true, stats: data.stats }); return; }
          if (name === 'error' || (name === 'job' && (data.state === 'failed' || data.state === 'cancelled'))) { onEvent(name === 'job' ? 'job' : 'error', data); close(); resolve({ ok: false, error: data.message || data.error }); return; }
          onEvent(name, data);
        });
        // ★ 后端会发的每种事件都要订阅上：addEventListener 没加的类型，
        //   SSE 推过来也不会进 onEvent（review / review_ready 就这么漏过一轮）。
        ['agent', 'node', 'log', 'job', 'done', 'error', 'review', 'review_ready'].forEach(on);

        es.onerror = () => {
          // 网络层错误：不立刻判死，等 3 秒看能不能重连上
          setTimeout(() => {
            if (es.readyState === EventSource.CLOSED) {
              onEvent('error', { code: 'SSE_LOST', message: '进度流断开，后端可能已退出' });
              resolve({ ok: false, error: 'SSE_LOST' });
            }
          }, 3000);
        };
      });
    },

    async cancel(jobId) {
      if (this.mode === 'mock') return;
      try {
        await fetch(this.base + '/api/jobs/' + jobId + '/cancel', { method: 'POST' });
      } catch (e) {}
    },

    /* ---------------- 单节点重生成 ---------------- */
    /**
     * 只重跑一个节点的素材。parts ∈ ['bg','char','voice']
     * 后端立刻返回 job_id，进度走 SSE（跟整篇生成一样的事件格式）。
     */
    async regenerateNode(projectId, nodeId, parts) {
      if (this.mode === 'mock') {
        await sleep(700);
        return { ok: true, mock: true };
      }
      try {
        const r = await fetch(this.base + '/api/projects/' + projectId +
          '/nodes/' + nodeId + '/regenerate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ parts: parts || ['bg', 'char', 'voice'] })
        });
        const b = await r.json().catch(function () { return {}; });
        if (!r.ok) {
          const e = b && b.error;
          return { ok: false, detail: (e && e.message) || ('HTTP ' + r.status), status: r.status };
        }
        return Object.assign({ ok: true }, b);
      } catch (e) {
        return { ok: false, detail: String((e && e.message) || e) };
      }
    },

    /**
     * 逐节点审核：记下一个节点的判定。
     * status ∈ ['pass','fail']；note 是驳回理由（可为空）。
     * 只写 node.review，不动剧情本身 —— 重画是另一个接口（regenerateNode）。
     */
    async reviewNode(projectId, nodeId, status, note) {
      if (this.mode === 'mock') {
        await sleep(120);
        return { ok: true, mock: true };
      }
      try {
        const r = await fetch(this.base + '/api/projects/' + projectId +
          '/nodes/' + nodeId + '/review', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: status, note: note || '' })
        });
        const b = await r.json().catch(function () { return {}; });
        if (!r.ok) {
          const e = b && b.error;
          return { ok: false, detail: (e && e.message) || ('HTTP ' + r.status) };
        }
        return Object.assign({ ok: true }, b);
      } catch (e) {
        return { ok: false, detail: String((e && e.message) || e) };
      }
    },

    /**
     * 批量审核：把还没审过的节点一次性标记。
     * 几百个节点一个个点不现实，面板上有「其余全部通过」走这条。
     * 已经审过的节点不会被覆盖。
     */
    async reviewBatch(projectId, status, note) {
      if (this.mode === 'mock') {
        await sleep(200);
        return { ok: true, mock: true, updated: 0 };
      }
      try {
        const r = await fetch(this.base + '/api/projects/' + projectId + '/nodes/review-batch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: status || 'pass', note: note || '' })
        });
        const b = await r.json().catch(function () { return {}; });
        if (!r.ok) {
          const e = b && b.error;
          return { ok: false, detail: (e && e.message) || ('HTTP ' + r.status) };
        }
        return Object.assign({ ok: true }, b);
      } catch (e) {
        return { ok: false, detail: String((e && e.message) || e) };
      }
    },

    /** 订阅一个 job 的事件流（生成/重生成共用一套事件） */
    watchJob(jobId, onEvent) {
      return new Promise((resolve) => {
        const url = this.base + '/api/jobs/' + jobId + '/events';
        let es;
        try { es = new EventSource(url); }
        catch (e) {
          onEvent('error', { code: 'SSE_FAILED', message: String(e.message || e) });
          resolve({ ok: false, error: 'SSE_FAILED' });
          return;
        }
        const close = function () { try { es.close(); } catch (e) {} };
        const on = (name) => es.addEventListener(name, function (ev) {
          let data = {};
          try { data = JSON.parse(ev.data); } catch (e) {}
          if (name === 'done' || (name === 'job' && data.state === 'done')) { onEvent('done', data); close(); resolve({ ok: true, stats: data.stats }); return; }
          if (name === 'error' || (name === 'job' && (data.state === 'failed' || data.state === 'cancelled'))) { onEvent(name === 'job' ? 'job' : 'error', data); close(); resolve({ ok: false, error: data.message || data.error }); return; }
          onEvent(name, data);
        });
        // ★ 后端会发的每种事件都要订阅上：addEventListener 没加的类型，
        //   SSE 推过来也不会进 onEvent（review / review_ready 就这么漏过一轮）。
        ['agent', 'node', 'log', 'job', 'done', 'error', 'review', 'review_ready'].forEach(on);
        es.onerror = function () {
          setTimeout(function () {
            if (es.readyState === EventSource.CLOSED) {
              onEvent('error', { code: 'SSE_LOST', message: '进度流断开，后端可能已退出' });
              resolve({ ok: false, error: 'SSE_LOST' });
            }
          }, 3000);
        };
      });
    },

    /* ---------------- 参考图 / 语音 / 素材库 ---------------- */
    async _assetRequest(path, options) {
      try {
        const r = await fetch(this.base + path, options || {});
        const body = await r.json().catch(function () { return {}; });
        if (!r.ok) {
          const e = body && body.error;
          const err = new Error((e && e.message) || ('请求失败 HTTP ' + r.status));
          err.code = (e && e.code) || ('HTTP_' + r.status);
          err.where = e && e.where;
          err.status = r.status;
          throw err;
        }
        return body;
      } catch (e) {
        if (e && e.status) throw e;
        const err = new Error('接口未连接：' + String((e && e.message) || e));
        err.code = 'NETWORK_ERROR';
        throw err;
      }
    },

    _mockAssets: { references: [], voices: [] },

    async listReferences(projectId) {
      if (this.mode === 'mock') return { ok: true, references: this._mockAssets.references.slice(), count: this._mockAssets.references.length };
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/references');
    },

    async uploadReference(projectId, file, kind, label, name, gender, characterId) {
      if (this.mode === 'mock') {
        await sleep(650);
        const item = { id: 'ref_' + Date.now().toString(36), kind: kind, label: label || file.name.replace(/\.[^.]+$/, ''),
          name: name || '', gender: gender || '', character_id: characterId || '', asset: '', bytes: file.size,
          created_at: new Date().toISOString(), mock: true };
        this._mockAssets.references.push(item);
        return { ok: true, reference: item };
      }
      const fd = new FormData();
      fd.append('file', file); fd.append('kind', kind);
      if (label) fd.append('label', label);
      if (name) fd.append('name', name);
      if (gender) fd.append('gender', gender);
      if (characterId) fd.append('character_id', characterId);
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/references', { method: 'POST', body: fd });
    },

    async renameReference(projectId, id, name, gender, characterId) {
      // characterId：把这张参考图挂到角色库里的某个角色（名字和性别由后端按角色补齐）
      if (this.mode === 'mock') {
        await sleep(200);
        var item = this._mockAssets.references.filter(function (x) { return x.id === id; })[0];
        if (item) {
          if (name !== undefined) item.name = name;
          if (gender !== undefined) item.gender = gender;
          if (characterId !== undefined) item.character_id = characterId;
        }
        return { ok: true, reference: item };
      }
      var patch = {};
      if (name !== undefined) patch.name = name;
      if (gender !== undefined) patch.gender = gender;
      if (characterId !== undefined) patch.character_id = characterId;
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/references/' + encodeURIComponent(id), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch)
      });
    },

    async deleteReference(projectId, id) {
      if (this.mode === 'mock') {
        await sleep(250);
        this._mockAssets.references = this._mockAssets.references.filter(function (x) { return x.id !== id; });
        return { ok: true, deleted_id: id };
      }
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/references/' + encodeURIComponent(id), { method: 'DELETE' });
    },

    async listVoices(projectId) {
      if (this.mode === 'mock') return { ok: true, voices: this._mockAssets.voices.slice(), count: this._mockAssets.voices.length };
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/voices');
    },

    async uploadVoice(projectId, file, characterId, label) {
      if (this.mode === 'mock') {
        await sleep(650);
        const item = { id: 'voi_' + Date.now().toString(36), character_id: characterId || '',
          label: label || file.name.replace(/\.[^.]+$/, ''), asset: '', seconds: 0, bytes: file.size,
          created_at: new Date().toISOString(), mock: true };
        this._mockAssets.voices.push(item);
        return { ok: true, voice: item };
      }
      const fd = new FormData();
      fd.append('file', file); fd.append('character_id', characterId || '');
      if (label) fd.append('label', label);
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/voices', { method: 'POST', body: fd });
    },

    async deleteLibraryItem(projectId, asset) {
      // 删「全部素材」里扫磁盘出来的一条（磁盘文件 + 对应登记）
      if (this.mode === 'mock') {
        await sleep(200);
        return { ok: true, deleted: asset };
      }
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/library?asset=' + encodeURIComponent(asset), { method: 'DELETE' });
    },

    async labelVoiceByAsset(projectId, asset, characterId) {
      // 给没登记的语音补标注（后端会顺手补一条登记）
      if (this.mode === 'mock') {
        await sleep(150);
        return { ok: true, voice: { asset: asset, character_id: characterId || '', id: 'voi_mock' } };
      }
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/voices/label-by-asset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset: asset, character_id: characterId || '' })
      });
    },

    async updateVoice(projectId, id, patch) {
      // 标注「这条语音是谁的声音」，生成 Agent 靠它把语音对上角色
      if (this.mode === 'mock') {
        await sleep(150);
        var item = this._mockAssets.voices.filter(function (x) { return x.id === id; })[0];
        if (item) {
          if (patch && patch.character_id !== undefined) item.character_id = patch.character_id;
          if (patch && patch.label !== undefined) item.label = patch.label;
        }
        return { ok: true, voice: item };
      }
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/voices/' + encodeURIComponent(id), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch || {})
      });
    },

    async deleteVoice(projectId, id) {
      if (this.mode === 'mock') {
        await sleep(250);
        this._mockAssets.voices = this._mockAssets.voices.filter(function (x) { return x.id !== id; });
        return { ok: true, deleted_id: id };
      }
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/voices/' + encodeURIComponent(id), { method: 'DELETE' });
    },

    async getLibrary(projectId) {
      if (this.mode === 'mock') {
        const refs = this._mockAssets.references.map(function (x) { return { name: x.label, asset: x.asset, bytes: x.bytes, mock: x.mock, ref: x }; });
        const voices = this._mockAssets.voices.map(function (x) { return { name: x.label, asset: x.asset, bytes: x.bytes, mock: x.mock, voice: x }; });
        return { ok: true, background: [], character: [], reference: refs, voice: voices };
      }
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/library');
    },

    /* ---------------- 角色库 ----------------
       角色是项目的「一等公民」：立绘和语音都只能挂到这里的角色上。
       数据源是 graph.json 的 characters，所以 Agent 和界面看到的是同一份。 */
    async listCharacters(projectId) {
      if (this.mode === 'mock') {
        await sleep(120);
        return { ok: true, characters: [{ id: 'heroine', name: '她', color: '#d946ef', gender: 'female', counts: { poses: 0, voices: 0, nodes: 0 } }], assignments: {}, graph_ready: true };
      }
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/characters');
    },

    async createCharacter(projectId, payload) {
      if (this.mode === 'mock') {
        await sleep(200);
        const item = Object.assign({ id: 'char_mock' + Date.now().toString(36), color: '#8b6bd6',
          counts: { poses: 0, voices: 0, nodes: 0 } }, payload || {});
        return { ok: true, character: item };
      }
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/characters', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
    },

    async updateCharacter(projectId, cid, payload) {
      if (this.mode === 'mock') {
        await sleep(150);
        return { ok: true, character: Object.assign({ id: cid }, payload || {}) };
      }
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/characters/' + encodeURIComponent(cid), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
    },

    async deleteCharacter(projectId, cid, force) {
      // force 不传时，后端发现还挂着立绘/语音/剧情会返回 409 并说明撞到多少处
      if (this.mode === 'mock') {
        await sleep(200);
        return { ok: true, id: cid, cleared: { poses: 0, voices: 0, nodes: 0 } };
      }
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/characters/' + encodeURIComponent(cid)
        + (force ? '?force=1' : ''), { method: 'DELETE' });
    },

    async assignCharacter(projectId, asset, characterId) {
      // 把一张立绘 / 一条语音挂到角色上（characterId 为空 = 取消挂载）
      if (this.mode === 'mock') {
        await sleep(150);
        return { ok: true, asset: asset, character_id: characterId || '', kind: asset.indexOf('/voice/') >= 0 ? 'voice' : 'pose' };
      }
      return this._assetRequest('/api/projects/' + encodeURIComponent(projectId) + '/characters/assign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset: asset, character_id: characterId || '' })
      });
    },

    /* ---------------- 资源路径 ---------------- */
    assetURL(projectId, relPath) {
      if (!relPath) return '';
      if (/^(?:data:|blob:|https?:)/.test(relPath)) return relPath;
      if (this.mode === 'mock') return '';       // mock 下用 CSS 占位
      return this.base + '/api/assets/' + encodeURIComponent(projectId) + '/' + relPath.replace(/^\/+/, '').split('/').map(encodeURIComponent).join('/');
    }
  };

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  global.GalAPI = GalAPI;

  // 读回上次的选择
  const savedMode = localStorage.getItem('gal_mode');
  if (savedMode === 'real') GalAPI.mode = 'real';
  const savedBase = localStorage.getItem('gal_base');
  if (savedBase) GalAPI.base = savedBase;

  // ?base=8791 可以临时指向另一个后端（比如 mock 后端）
  const qs = new URLSearchParams(location.search);
  const baseParam = qs.get('base');
  if (baseParam) {
    GalAPI.base = /^https?:/.test(baseParam) ? baseParam : 'http://127.0.0.1:' + baseParam;
  }

})(window);
