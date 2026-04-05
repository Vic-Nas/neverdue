// project/static/manual/js/pages/rules.js
(function () {
  function getCsrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content && meta.content !== 'NOTPROVIDED') return meta.content;
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }

  var pageEl = document.querySelector('.page[data-rule-delete-url-tpl]');
  var DELETE_URL_TPL = pageEl ? pageEl.dataset.ruleDeleteUrlTpl : '';

  // ─── Delete ───────────────────────────────────────────────────────────────

  function deleteRule(pk, card) {
    fetch(DELETE_URL_TPL.replace('__PK__', pk), {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrf() },
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) card.remove();
        else alert('Error: ' + data.error);
      })
      .catch(function () { alert('Network error.'); });
  }

  document.querySelectorAll('.rule-delete-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var card = btn.closest('.rule-card');
      deleteRule(btn.dataset.pk, card);
    });
  });

  // ─── Select mode / bulk delete ─────────────────────────────────────────────

  var selectToggle = document.getElementById('rule-select-toggle');
  var bulkBar = document.getElementById('rule-bulk-bar');
  var selectedCountEl = document.getElementById('rule-selected-count');
  var bulkDeleteBtn = document.getElementById('rule-bulk-delete-btn');
  var selectAllBtn = document.getElementById('rule-select-all-btn');
  var selecting = false;

  function getCheckboxes() {
    return document.querySelectorAll('.rule-card__checkbox');
  }

  function getChecked() {
    return Array.from(getCheckboxes()).filter(function (c) { return c.checked; });
  }

  function updateCount() {
    var count = getChecked().length;
    if (selectedCountEl) selectedCountEl.textContent = count;
    if (selectAllBtn) selectAllBtn.textContent = count === getCheckboxes().length ? 'Deselect all' : 'Select all';
  }

  function enterSelectMode() {
    selecting = true;
    document.body.classList.add('selecting-rules');
    if (bulkBar) bulkBar.classList.add('visible');
    if (selectToggle) selectToggle.textContent = 'Done';
    updateCount();
  }

  function exitSelectMode() {
    selecting = false;
    document.body.classList.remove('selecting-rules');
    getCheckboxes().forEach(function (c) { c.checked = false; });
    document.querySelectorAll('.rule-card--selected').forEach(function (el) { el.classList.remove('rule-card--selected'); });
    if (bulkBar) bulkBar.classList.remove('visible');
    if (selectToggle) selectToggle.textContent = 'Select';
    updateCount();
  }

  if (selectToggle) {
    selectToggle.addEventListener('click', function () {
      selecting ? exitSelectMode() : enterSelectMode();
    });
  }

  // Click card to toggle checkbox in select mode
  document.querySelectorAll('.rule-card').forEach(function (card) {
    card.addEventListener('click', function (e) {
      if (!selecting) return;
      if (e.target.closest('button, a')) return;
      e.preventDefault();
      var cb = card.querySelector('.rule-card__checkbox');
      if (cb) {
        cb.checked = !cb.checked;
        card.classList.toggle('rule-card--selected', cb.checked);
        updateCount();
      }
    });
  });

  getCheckboxes().forEach(function (cb) {
    cb.addEventListener('change', function () {
      var card = cb.closest('.rule-card');
      if (card) card.classList.toggle('rule-card--selected', cb.checked);
      updateCount();
    });
  });

  if (selectAllBtn) {
    selectAllBtn.addEventListener('click', function () {
      var cbs = getCheckboxes();
      var allChecked = Array.from(cbs).every(function (c) { return c.checked; });
      cbs.forEach(function (c) {
        c.checked = !allChecked;
        var card = c.closest('.rule-card');
        if (card) card.classList.toggle('rule-card--selected', c.checked);
      });
      updateCount();
    });
  }

  if (bulkDeleteBtn && bulkBar) {
    bulkDeleteBtn.addEventListener('click', function () {
      var ids = getChecked().map(function (c) { return parseInt(c.value, 10); });
      if (!ids.length) return;
      if (!confirm('Delete ' + ids.length + ' rule' + (ids.length !== 1 ? 's' : '') + '?')) return;
      var url = bulkBar.dataset.bulkUrl;
      bulkDeleteBtn.disabled = true;
      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
        body: JSON.stringify({ ids: ids }),
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) location.reload();
          else { alert(data.error || 'Delete failed.'); bulkDeleteBtn.disabled = false; }
        })
        .catch(function () { alert('Network error.'); bulkDeleteBtn.disabled = false; });
    });
  }
})();