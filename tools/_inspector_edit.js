(function () {
  var out = {};
  function el(field) {
    return Array.prototype.filter.call(
      document.querySelectorAll('[data-f]'),
      function (x) { return x.dataset.f === field; })[0];
  }
  function fire(field, val, ev) {
    var n = el(field);
    if (!n) { out['missing:' + field] = true; return; }
    n.value = val;
    n.dispatchEvent(new Event(ev || 'input', { bubbles: true }));
  }

  fire('bg.prompt', '改过的提示词 ABC');
  out.bgPrompt = GalFrontendTest.inspect('n4').bg.prompt;

  fire('chars.0.pos', 'right', 'change');
  out.char0pos = GalFrontendTest.inspect('n4').chars[0].pos;

  fire('lines.1.text', '第二句改掉了');
  out.line1 = GalFrontendTest.inspect('n4').lines[1].text;

  fire('title', '新标题');
  out.titleShown = document.getElementById('insTitle').textContent;

  fire('chars.1.mood', '紧张');
  out.char1mood = GalFrontendTest.inspect('n4').chars[1].mood;

  fire('bg.place', '车站');
  out.bgPlace = GalFrontendTest.inspect('n4').bg.place;

  var before = GalFrontendTest.inspect('n4').lines.length;
  var addBtn = document.querySelector('[data-act="line-add"]');
  if (addBtn) addBtn.click();
  out.linesBefore = before;
  out.linesAfter = GalFrontendTest.inspect('n4').lines.length;

  var delBtns = document.querySelectorAll('[data-act="char-del"]');
  out.charDelButtons = delBtns.length;
  if (delBtns.length) delBtns[1].click();
  out.charsAfterDel = GalFrontendTest.inspect('n4').chars.length;

  return JSON.stringify(out);
})()
