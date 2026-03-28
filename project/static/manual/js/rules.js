(function () {
  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  var pageEl = document.querySelector('.page[data-rule-add-url]');
  var ADD_URL = pageEl ? pageEl.dataset.ruleAddUrl : '';
  var DELETE_URL_TPL = pageEl ? pageEl.dataset.ruleDeleteUrlTpl : '';

  function deleteRule(pk, row) {
    fetch(DELETE_URL_TPL.replace('__PK__', pk), {
      method: 'POST',
      headers: { 'X-CSRFToken': CSRF },
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) row.remove();
        else alert('Error: ' + data.error);
      })
      .catch(function () { alert('Network error.'); });
  }

  document.querySelectorAll('.rule-delete-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var row = btn.closest('.rule-row');
      deleteRule(btn.dataset.pk, row);
    });
  });

  function addRule(form, ruleType) {
    var data = { rule_type: ruleType };

    if (ruleType === 'prompt') {
      data.prompt_text = form.querySelector('[name="prompt_text"]').value.trim();
      data.pattern = (form.querySelector('[name="prompt_pattern"]') || { value: '' }).value.trim();
    } else {
      data.pattern = form.querySelector('[name="pattern"]').value.trim();
      data.action = form.querySelector('[name="action"]').value;
      var catSelect = form.querySelector('[name="category_id"]');
      if (catSelect) data.category_id = catSelect.value || null;
    }

    var btn = form.querySelector('button[type="submit"]');
    btn.disabled = true;

    fetch(ADD_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify(data),
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.ok) location.reload();
        else { alert('Error: ' + res.error); btn.disabled = false; }
      })
      .catch(function () { alert('Network error.'); btn.disabled = false; });
  }

  document.querySelectorAll('.rule-add-form').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      addRule(form, form.dataset.ruleType);
    });
  });

  // Show/hide category select based on action
  document.querySelectorAll('select[name="action"]').forEach(function (sel) {
    function toggle() {
      var catGroup = sel.closest('form').querySelector('.cat-select-group');
      if (catGroup) catGroup.style.display = sel.value === 'categorize' ? '' : 'none';
    }
    sel.addEventListener('change', toggle);
    toggle();
  });
})();