(async function () {
  var out = {};
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  GalFrontendTest.setLibrary({ ok: true, background: [], character: [], voice: [] });

  // 给 n4 两句台词，说话人都在角色表里
  var r = GalFrontendTest.inspect('n4');
  r.lines = [
    { who: 'heroine', text: '第一句', voice: null, dur: 1.2 },
    { who: 'him', text: '第二句', voice: null, dur: 1.1 }
  ];
  GalFrontendTest.select('n4');
  await sleep(150);

  var inputs = document.querySelectorAll('[data-f^="__voice."]');
  out.voiceInputs = inputs.length;
  out.hasDatalist = !!document.getElementById('voicePresets');
  out.presetCount = document.querySelectorAll('#voicePresets option').length;

  if (inputs.length) {
    inputs[0].value = 'Serena';
    inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
    await sleep(100);
  }
  var chars = GalFrontendTest.characters();
  out.heroineVoiced = (chars.filter(function (c) { return c.id === 'heroine'; })[0] || {}).voice_id;
  out.himVoiced = (chars.filter(function (c) { return c.id === 'him'; })[0] || {}).voice_id;

  // 把面板高度放开，让截图能看到全部区块（否则音色区在滚动区外）
  var ib = document.querySelector('.inspector .ib');
  if (ib) ib.style.maxHeight = 'none';
  var ins = document.getElementById('inspector');
  if (ins) ins.style.width = '382px';

  return JSON.stringify(out);
})()
