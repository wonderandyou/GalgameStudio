/* ============================================================
   鲸落工坊 · 接口层
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

    async getGraph(projectId) {
      if (this.mode === 'mock') return null;
      const r = await fetch(this.base + '/api/projects/' + projectId + '/graph');
      if (!r.ok) throw new Error('读取项目图失败 HTTP ' + r.status);
      return await r.json();
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

    /** 导出：format = renpy | web | video */
    async exportProject(projectId, format) {
      if (this.mode === 'mock') {
        await sleep(900 + Math.random() * 700);
        return { ok: true, out: 'D:\\GalgameStudio\\out\\雨停之前\\export\\' + format, mock: true };
      }
      try {
        const r = await fetch(this.base + '/api/projects/' + projectId + '/export', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ format: format })
        });
        if (!r.ok) return { ok: false, detail: 'HTTP ' + r.status };
        return await r.json();
      } catch (e) {
        return { ok: false, detail: String(e.message || e) };
      }
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
        if (!r.ok) throw new Error('启动生成失败 HTTP ' + r.status);
        const j = await r.json();
        jobId = j.job_id;
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
          if (name === 'done') { onEvent('done', data); close(); resolve({ ok: true, stats: data.stats }); return; }
          if (name === 'error') { onEvent('error', data); close(); resolve({ ok: false, error: data.message }); return; }
          onEvent(name, data);
        });
        ['agent', 'node', 'log', 'done', 'error'].forEach(on);

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

    /* ---------------- 资源路径 ---------------- */
    assetURL(projectId, relPath) {
      if (!relPath) return '';
      if (this.mode === 'mock') return '';       // mock 下用 CSS 占位
      return this.base + '/api/assets/' + projectId + '/' + relPath.replace(/^\/+/, '');
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
