(function () {
  var helps = document.querySelectorAll('[data-help]');
  var opens = document.querySelectorAll('[data-open-url]');
  if (helps[0]) helps[0].click();
  var mask = document.querySelector('.guide-mask');
  var out = {
    helpButtons: helps.length,
    openButtons: opens.length,
    guideShown: !!mask,
    guideTitle: mask ? mask.querySelector('.guide-head b').textContent : null,
    stepCount: mask ? mask.querySelectorAll('.guide-steps li').length : 0,
    noteText: mask ? (mask.querySelector('.guide-note') || {}).textContent : null,
    firstOpenUrl: opens.length ? opens[0].dataset.openUrl : null
  };
  return JSON.stringify(out);
})()
