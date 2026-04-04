// project/static/manual/js/pages/dashboard.js

// ── Copy inbox address ──
(function () {
  var btn = document.getElementById('copy-inbox-btn');
  var addr = document.getElementById('inbox-address');
  if (!btn || !addr) return;
  btn.addEventListener('click', function () {
    navigator.clipboard.writeText(addr.textContent.trim()).then(function () {
      btn.textContent = 'Copied!';
      setTimeout(function () { btn.textContent = 'Copy'; }, 1500);
    });
  });
})();

// ── Select mode & bulk actions ──
(function () {
  var bulkBar = document.getElementById('bulk-bar');
  if (!bulkBar) return;

  var BULK_URL = bulkBar.dataset.bulkUrl;
  var EXPORT_BASE_URL = bulkBar.dataset.exportUrl;
  var CSRF = document.querySelector('meta[name="csrf-token"]').content;

  var selectToggle = document.getElementById('select-toggle');
  var selectedCountEl = document.getElementById('selected-count');
  var selectAllBtn = document.getElementById('select-all-btn');
  var bulkDeleteBtn = document.getElementById('bulk-delete-btn');
  var bulkReprocessBtn = document.getElementById('bulk-reprocess-btn');
  var reprocessDrawer = document.getElementById('reprocess-drawer');
  var reprocessPrompt = document.getElementById('reprocess-prompt');
  var reprocessConfirm = document.getElementById('reprocess-confirm-btn');
  var reprocessCancel = document.getElementById('reprocess-cancel-btn');

  var selecting = false;

  function getCheckboxes() {
    return document.querySelectorAll('.event-card__checkbox');
  }

  function getChecked() {
    return [...getCheckboxes()].filter(function (c) { return c.checked; });
  }

  function getSelectedIds() {
    return getChecked().map(function (c) { return parseInt(c.value); });
  }

  function updateBulkBar() {
    var count = getChecked().length;
    var ids = getSelectedIds();
    selectedCountEl.textContent = count;
    bulkBar.classList.toggle('visible', count > 0);
    selectAllBtn.textContent = count === getCheckboxes().length ? 'Deselect all' : 'Select all';

    var exportBtn = document.getElementById('export-btn');
    if (count > 0) {
      exportBtn.style.display = 'inline-block';
      exportBtn.href = EXPORT_BASE_URL + '?ids=' + ids.join(',');
    } else {
      exportBtn.style.display = 'none';
    }
  }

  function enterSelectMode() {
    selecting = true;
    document.body.classList.add('selecting');
    if (selectToggle) selectToggle.textContent = 'Done';
  }

  function exitSelectMode() {
    selecting = false;
    document.body.classList.remove('selecting');
    if (selectToggle) selectToggle.textContent = 'Select';
    getCheckboxes().forEach(function (c) { c.checked = false; });
    bulkBar.classList.remove('visible');
    reprocessDrawer.classList.remove('open');
  }

  if (selectToggle) {
    selectToggle.addEventListener('click', function () {
      if (selecting) exitSelectMode(); else enterSelectMode();
    });
  }

  document.querySelectorAll('.event-card').forEach(function (card) {
    card.addEventListener('click', function (e) {
      if (!selecting) return;
      e.preventDefault();
      var cb = card.querySelector('.event-card__checkbox');
      if (cb) {
        cb.checked = !cb.checked;
        card.classList.toggle('event-card--selected', cb.checked);
        updateBulkBar();
      }
    });
  });

  getCheckboxes().forEach(function (cb) {
    cb.addEventListener('change', function () {
      var card = cb.closest('.event-card');
      if (card) card.classList.toggle('event-card--selected', cb.checked);
      updateBulkBar();
    });
  });

  selectAllBtn && selectAllBtn.addEventListener('click', function () {
    var cbs = getCheckboxes();
    var allChecked = [...cbs].every(function (c) { return c.checked; });
    cbs.forEach(function (c) {
      c.checked = !allChecked;
      var card = c.closest('.event-card');
      if (card) card.classList.toggle('event-card--selected', c.checked);
    });
    updateBulkBar();
  });

  bulkDeleteBtn && bulkDeleteBtn.addEventListener('click', async function () {
    var ids = getSelectedIds();
    if (!ids.length) return;
    if (!confirm('Delete ' + ids.length + ' event' + (ids.length !== 1 ? 's' : '') + '? This cannot be undone.')) return;
    var res = await fetch(BULK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ action: 'delete', ids }),
    });
    var data = await res.json();
    if (data.ok) location.reload();
    else alert('Error: ' + data.error);
  });

  bulkReprocessBtn && bulkReprocessBtn.addEventListener('click', function () {
    reprocessDrawer.classList.toggle('open');
  });

  reprocessCancel && reprocessCancel.addEventListener('click', function () {
    reprocessDrawer.classList.remove('open');
  });

  reprocessConfirm && reprocessConfirm.addEventListener('click', async function () {
    var ids = getSelectedIds();
    var prompt = reprocessPrompt.value.trim();
    if (!ids.length) return;
    if (!prompt) { reprocessPrompt.focus(); return; }

    reprocessConfirm.disabled = true;
    reprocessConfirm.textContent = 'Queuing…';

    var res = await fetch(BULK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ action: 'reprocess', ids, prompt }),
    });
    var data = await res.json();
    if (data.ok) {
      exitSelectMode();
      var toast = document.createElement('div');
      toast.textContent = data.queued + ' event' + (data.queued !== 1 ? 's' : '') + ' queued for re-processing.';
      toast.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1f2937;color:#fff;padding:10px 18px;border-radius:8px;font-size:0.9rem;z-index:999';
      document.body.appendChild(toast);
      setTimeout(function () { location.reload(); }, 1800);
    } else {
      alert('Error: ' + data.error);
      reprocessConfirm.disabled = false;
      reprocessConfirm.textContent = 'Re-process';
    }
  });
})();
