(function () {
  var assets = document.querySelector('[data-view="assets"]');
  if (assets) assets.click();
  var g = function (id) { var el = document.getElementById(id); return el ? true : false; };
  var fields = document.getElementById('refCharFields');
  var hint = document.getElementById('refCharHint');
  return JSON.stringify({
    assetsView: !!assets,
    nameInput: g('refCharName'),
    genderSelect: g('refCharGender'),
    fieldsHidden: fields ? fields.hidden : null,
    hintHidden: hint ? hint.hidden : null,
    genderOptions: document.querySelectorAll('#refCharGender option').length
  });
})()
