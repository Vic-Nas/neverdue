// project/static/manual/js/pages/categories.js
(function () {
  var bulkBar = document.getElementById('cat-bulk-bar');
  if (!bulkBar) return;

  var BULK_URL = bulkBar.dataset.bulkUrl;
  var CSRF = document.querySelector('meta[name="csrf-token"]').content;

  var selectToggle = document.getElementById('cat-select-toggle');
  var selectedCountEl = document.getElementById('cat-selected-count');
  var selectAllBtn = document.getElementById('cat-select-all-btn');
  var bulkDeleteBtn = document.getElementById('cat-bulk-delete-btn');

  var selecting = false;

  function getCheckboxes() {
    return document.querySelectorAll('.cat-card__checkbox');
  }

  function getChecked() {
    return [...getCheckboxes()].filter(function (c) { return c.checked; });
  }

  function getSelectedIds() {
    return getChecked().map(function (c) { return parseInt(c.value); });
  }

  function updateBulkBar() {
    var count = getChecked().length;
    selectedCountEl.textContent = count;
    bulkBar.classList.toggle('visible', count > 0);
    selectAllBtn.textContent = count === getCheckboxes().length ? 'Deselect all' : 'Select all';
  }

  function enterSelectMode() {
    selecting = true;
    document.body.classList.add('cat-selecting');
    if (selectToggle) selectToggle.textContent = 'Done';
  }

  function exitSelectMode() {
    selecting = false;
    document.body.classList.remove('cat-selecting');
    if (selectToggle) selectToggle.textContent = 'Select';
    getCheckboxes().forEach(function (c) { c.checked = false; });
    bulkBar.classList.remove('visible');
  }

  if (selectToggle) {
    selectToggle.addEventListener('click', function () {
      if (selecting) exitSelectMode(); else enterSelectMode();
    });
  }

  document.querySelectorAll('.cat-card').forEach(function (card) {
    card.addEventListener('click', function (e) {
      if (!selecting) return;
      if (e.target.closest('a, button')) return;
      e.preventDefault();
      var cb = card.querySelector('.cat-card__checkbox');
      if (cb) {
        cb.checked = !cb.checked;
        card.classList.toggle('cat-card--selected', cb.checked);
        updateBulkBar();
      }
    });
  });

  getCheckboxes().forEach(function (cb) {
    cb.addEventListener('change', function () {
      var card = cb.closest('.cat-card');
      if (card) card.classList.toggle('cat-card--selected', cb.checked);
      updateBulkBar();
    });
  });

  selectAllBtn && selectAllBtn.addEventListener('click', function () {
    var cbs = getCheckboxes();
    var allChecked = [...cbs].every(function (c) { return c.checked; });
    cbs.forEach(function (c) {
      c.checked = !allChecked;
      var card = c.closest('.cat-card');
      if (card) card.classList.toggle('cat-card--selected', c.checked);
    });
    updateBulkBar();
  });

  bulkDeleteBtn && bulkDeleteBtn.addEventListener('click', async function () {
    var ids = getSelectedIds();
    if (!ids.length) return;
    if (!confirm('Delete ' + ids.length + ' categor' + (ids.length !== 1 ? 'ies' : 'y') + ' and all their events? This cannot be undone.')) return;
    var res = await fetch(BULK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ ids: ids }),
    });
    var data = await res.json();
    if (data.ok) location.reload();
    else alert('Error: ' + (data.error || 'Unknown error'));
  });
})();
