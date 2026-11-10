(function () {
  document.querySelector('[data-view="editor"]').click();
  var g = function (id) { var el = document.getElementById(id); return el ? el.textContent : null; };
  return JSON.stringify({
    nodes: g('lblNodes'),
    estimate: g('lblEstimate'),
    keyLabel: g('lblKey'),
    flavorLabel: g('lblFlavor'),
    depthLabel: g('lblBranch'),
    minutesSliderGone: !document.getElementById('rngMinutes'),
    keyExists: !!document.getElementById('rngKey'),
    flavorExists: !!document.getElementById('rngFlavor'),
    refCharFields: !!document.getElementById('refCharFields'),
    refCharHidden: (document.getElementById('refCharFields') || {}).hidden
  });
})()
