/* js/pages/categories.js — categories list page */

(function () {
  'use strict';

  function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : '';
  }

  const selectToggle = document.getElementById('cat-select-toggle');
  const bulkBar      = document.getElementById('cat-bulk-bar');
  const countEl      = document.getElementById('cat-selected-count');
  const selectAllBtn = document.getElementById('cat-select-all-btn');
  const deleteBtn    = document.getElementById('cat-bulk-delete-btn');
  const catList      = document.querySelector('.cat-list');

  let selecting = false;

  function getChecked() {
    return catList ? [...catList.querySelectorAll('.cat-card__checkbox:checked')] : [];
  }

  function updateBar() {
    if (countEl) countEl.textContent = getChecked().length;
    if (bulkBar) bulkBar.classList.toggle('is-active', selecting);
  }

  if (selectToggle && catList) {
    selectToggle.addEventListener('click', () => {
      selecting = !selecting;
      selectToggle.textContent = selecting ? 'Done' : 'Select';
      if (!selecting) catList.querySelectorAll('.cat-card__checkbox').forEach(cb => { cb.checked = false; });
      updateBar();
    });
    catList.addEventListener('change', (e) => {
      if (e.target.classList.contains('cat-card__checkbox')) updateBar();
    });
  }

  if (selectAllBtn) {
    selectAllBtn.addEventListener('click', () => {
      const all = catList ? [...catList.querySelectorAll('.cat-card__checkbox')] : [];
      const allChecked = all.every(cb => cb.checked);
      all.forEach(cb => { cb.checked = !allChecked; });
      updateBar();
    });
  }

  if (deleteBtn && bulkBar) {
    deleteBtn.addEventListener('click', () => {
      const ids = getChecked().map(cb => cb.value);
      if (!ids.length) return;
      if (!confirm(`Delete ${ids.length} categor${ids.length === 1 ? 'y' : 'ies'}? Events will be uncategorized.`)) return;

      fetch(bulkBar.dataset.bulkUrl, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrf(),
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({ ids }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.ok) {
            ids.forEach(id => {
              const card = catList.querySelector(`.cat-card[data-cat-id="${id}"]`);
              if (card) card.remove();
            });
            updateBar();
          }
        })
        .catch(() => alert('Error deleting categories.'));
    });
  }

})();
