(async function () {
  var out = {};
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };

  GalFrontendTest.setLibrary({
    ok: true,
    background: [
      { name: 'bg/roof_night.png', asset: 'assets/bg/roof_night.png' },
      { name: 'bg/station.png', asset: 'assets/bg/station.png' }
    ],
    character: [{ name: 'char/heroine_stand.png', asset: 'assets/char/heroine_stand.png' }],
    voice: [{ name: 'voice/line_01.wav', asset: 'assets/voice/line_01.wav' }]
  });

  // ① 走真按钮：「背景 · 从素材库挑」→ 选第二张
  var pickBtn = document.querySelector('[data-act="pick-bg"]');
  out.hasPickBtn = !!pickBtn;
  pickBtn.click();
  await sleep(150);
  out.pickerItems = document.querySelectorAll('.picker-item').length;
  out.pickerVisible = !document.getElementById('pickerMask').hidden;
  var items = document.querySelectorAll('.picker-item');
  items[1].click();
  await sleep(200);
  var r4 = GalFrontendTest.inspect('n4');
  out.bgAfterPick = (r4.bg || {}).asset;
  out.bgProvider = (r4.bg || {}).provider;
  out.pickerClosed = document.getElementById('pickerMask').hidden;

  // ② 立绘换图（也走按钮）
  var poseBtn = document.querySelector('[data-act="pick-pose"]');
  out.hasPoseBtn = !!poseBtn;
  if (poseBtn) {
    poseBtn.click();
    await sleep(150);
    var pi = document.querySelectorAll('.picker-item');
    out.poseItems = pi.length;
    if (pi.length) pi[0].click();
    await sleep(200);
    out.poseAfter = GalFrontendTest.inspect('n4').chars[0].pose;
  }

  // ③ 语音换（走按钮）
  var voiceBtn = document.querySelector('[data-act="pick-voice"]');
  out.hasVoiceBtn = !!voiceBtn;
  if (voiceBtn) {
    voiceBtn.click();
    await sleep(150);
    var vi = document.querySelectorAll('.picker-item');
    out.voiceItems = vi.length;
    if (vi.length) vi[0].click();
    await sleep(200);
    out.voiceAfter = GalFrontendTest.inspect('n4').lines[0].voice;
  }

  // ④ 节点类型：场景 → 结局 → 分支 → 场景
  GalFrontendTest.changeType('n4', 'ending');
  await sleep(80);
  var e1 = GalFrontendTest.inspect('n4');
  out.toEnding = { type: e1.type, endKind: e1.end_kind, hasChoices: Object.prototype.hasOwnProperty.call(e1, 'choices') };

  GalFrontendTest.changeType('n4', 'choice');
  await sleep(80);
  var e2 = GalFrontendTest.inspect('n4');
  out.toChoice = { type: e2.type, branchKind: e2.branch_kind, choices: (e2.choices || []).length };

  GalFrontendTest.changeType('n4', 'scene');
  await sleep(80);
  var e3 = GalFrontendTest.inspect('n4');
  out.backToScene = { type: e3.type, endKind: e3.end_kind, branchKind: e3.branch_kind };

  // ⑤ 分支选项改目标：拿一个真分支节点，塞两条选项，选中它再改
  var choiceId = null;
  GalEditor.dump().nodes.forEach(function (n) { if (n.type === 'choice' && !choiceId) choiceId = n.id; });
  out.choiceId = choiceId;
  if (choiceId) {
    var rc = GalFrontendTest.inspect(choiceId);
    rc.choices = [{ label: '走左边', to: '' }, { label: '走右边', to: '' }];
    GalFrontendTest.select(choiceId);      // 重新渲染面板（同类型时 changeType 会早退）
    await sleep(120);
    var toSel = Array.prototype.filter.call(
      document.querySelectorAll('[data-f]'),
      function (x) { return x.dataset.f === 'choices.0.to'; })[0];
    out.hasToSelect = !!toSel;
    if (toSel) {
      toSel.value = 'n7';
      toSel.dispatchEvent(new Event('change', { bubbles: true }));
      await sleep(150);
    }
    var rc2 = GalFrontendTest.inspect(choiceId);
    out.choiceTo = (rc2.choices || [])[0] && (rc2.choices || [])[0].to;
    out.linksFromChoice = GalEditor.dump().links.filter(function (l) { return l.from === choiceId; });
  }

  return JSON.stringify(out);
})()
