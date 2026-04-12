/* js/pages/dashboard.js — main event list page */

(function () {
  'use strict';

  function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : '';
  }

  // ── Select mode ────────────────────────────────────────────────────────────
  const selectToggle = document.getElementById('select-toggle');
  const eventList    = document.getElementById('event-list');
  const bulkBar      = document.getElementById('bulk-bar');
  const selectedCount = document.getElementById('selected-count');
  const selectAllBtn = document.getElementById('select-all-btn');
  const exportBtn    = document.getElementById('export-btn');

  let selecting = false;

  function getChecked() {
    return eventList ? [...eventList.querySelectorAll('.event-card__checkbox:checked')] : [];
  }

  function updateBulkBar() {
    const checked = getChecked();
    if (selectedCount) selectedCount.textContent = checked.length;
    if (bulkBar) bulkBar.classList.toggle('is-active', selecting);

    // Export button — build ICS URL from checked IDs
    if (exportBtn && bulkBar) {
      const ids = checked.map(cb => cb.value);
      if (ids.length) {
        const exportUrl = bulkBar.dataset.exportUrl;
        exportBtn.href = exportUrl + '?ids=' + ids.join(',');
        exportBtn.style.display = '';
      } else {
        exportBtn.style.display = 'none';
      }
    }
  }

  if (selectToggle && eventList) {
    selectToggle.addEventListener('click', () => {
      selecting = !selecting;
      eventList.classList.toggle('is-selecting', selecting);
      selectToggle.textContent = selecting ? 'Done' : 'Select';
      if (!selecting) {
        eventList.querySelectorAll('.event-card__checkbox').forEach(cb => { cb.checked = false; });
      }
      updateBulkBar();
    });

    eventList.addEventListener('change', (e) => {
      if (e.target.classList.contains('event-card__checkbox')) updateBulkBar();
    });
  }

  if (selectAllBtn) {
    selectAllBtn.addEventListener('click', () => {
      const all = eventList ? [...eventList.querySelectorAll('.event-card__checkbox')] : [];
      const allChecked = all.every(cb => cb.checked);
      all.forEach(cb => { cb.checked = !allChecked; });
      updateBulkBar();
    });
  }

  // ── Bulk delete ────────────────────────────────────────────────────────────
  const bulkDeleteBtn = document.getElementById('bulk-delete-btn');
  if (bulkDeleteBtn && bulkBar) {
    bulkDeleteBtn.addEventListener('click', () => {
      const ids = getChecked().map(cb => cb.value);
      if (!ids.length) return;
      if (!confirm(`Delete ${ids.length} event(s)? This cannot be undone.`)) return;

      fetch(bulkBar.dataset.bulkUrl, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrf(),
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({ action: 'delete', ids }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.ok) {
            ids.forEach(id => {
              const card = eventList.querySelector(`.event-card[data-id="${id}"]`);
              if (card) card.closest('.event-card-wrap')?.remove();
            });
            updateBulkBar();
          }
        })
        .catch(() => alert('Error deleting events.'));
    });
  }

  // ── Bulk reprocess ────────────────────────────────────────────────────────
  const reprocessBtn    = document.getElementById('bulk-reprocess-btn');
  const reprocessDrawer = document.getElementById('reprocess-drawer');
  const reprocessConfirm = document.getElementById('reprocess-confirm-btn');
  const reprocessCancel  = document.getElementById('reprocess-cancel-btn');
  const reprocessPrompt  = document.getElementById('reprocess-prompt');

  if (reprocessBtn && reprocessDrawer) {
    reprocessBtn.addEventListener('click', () => {
      reprocessDrawer.classList.toggle('is-open');
    });
  }
  if (reprocessCancel) {
    reprocessCancel.addEventListener('click', () => reprocessDrawer.classList.remove('is-open'));
  }
  if (reprocessConfirm && bulkBar) {
    reprocessConfirm.addEventListener('click', () => {
      const ids = getChecked().map(cb => cb.value);
      if (!ids.length) { alert('Select at least one event.'); return; }
      const prompt = reprocessPrompt ? reprocessPrompt.value.trim() : '';

      reprocessConfirm.disabled = true;
      reprocessConfirm.textContent = 'Sending…';

      fetch(bulkBar.dataset.bulkUrl, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrf(),
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({ action: 'reprocess', ids, prompt }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.ok) {
            window.location.reload();
          } else {
            alert(data.error || 'Error re-processing events.');
            reprocessConfirm.disabled = false;
            reprocessConfirm.textContent = 'Re-process';
          }
        })
        .catch(() => {
          alert('Network error.');
          reprocessConfirm.disabled = false;
          reprocessConfirm.textContent = 'Re-process';
        });
    });
  }

  // ── Copy inbox address ────────────────────────────────────────────────────
  const copyInboxBtn = document.getElementById('copy-inbox-btn');
  const inboxAddr    = document.getElementById('inbox-address');
  if (copyInboxBtn && inboxAddr) {
    copyInboxBtn.addEventListener('click', () => {
      window.copyText
        ? window.copyText(inboxAddr.textContent.trim(), copyInboxBtn)
        : navigator.clipboard.writeText(inboxAddr.textContent.trim());
    });
  }

})();
