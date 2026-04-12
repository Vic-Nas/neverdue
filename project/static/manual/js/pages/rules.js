/* js/pages/rules.js — rules list page */

(function () {
  'use strict';

  function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : '';
  }

  const page         = document.querySelector('[data-rule-delete-url-tpl]');
  const deleteTpl    = page ? page.dataset.ruleDeleteUrlTpl : null;
  const selectToggle = document.getElementById('rule-select-toggle');
  const bulkBar      = document.getElementById('rule-bulk-bar');
  const countEl      = document.getElementById('rule-selected-count');
  const selectAllBtn = document.getElementById('rule-select-all-btn');
  const deleteBulk   = document.getElementById('rule-bulk-delete-btn');
  const ruleList     = document.querySelector('.rule-list');

  let selecting = false;

  function getChecked() {
    return ruleList ? [...ruleList.querySelectorAll('.rule-card__checkbox:checked')] : [];
  }

  function updateBar() {
    if (countEl) countEl.textContent = getChecked().length;
    if (bulkBar) bulkBar.classList.toggle('is-active', selecting);
  }

  if (selectToggle && ruleList) {
    selectToggle.addEventListener('click', () => {
      selecting = !selecting;
      selectToggle.textContent = selecting ? 'Done' : 'Select';
      if (!selecting) ruleList.querySelectorAll('.rule-card__checkbox').forEach(cb => { cb.checked = false; });
      updateBar();
    });
    ruleList.addEventListener('change', (e) => {
      if (e.target.classList.contains('rule-card__checkbox')) updateBar();
    });
  }

  if (selectAllBtn) {
    selectAllBtn.addEventListener('click', () => {
      const all = ruleList ? [...ruleList.querySelectorAll('.rule-card__checkbox')] : [];
      const allChecked = all.every(cb => cb.checked);
      all.forEach(cb => { cb.checked = !allChecked; });
      updateBar();
    });
  }

  if (deleteBulk && bulkBar) {
    deleteBulk.addEventListener('click', () => {
      const ids = getChecked().map(cb => cb.value);
      if (!ids.length) return;
      if (!confirm(`Delete ${ids.length} rule(s)?`)) return;

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
              const card = ruleList.querySelector(`.rule-card[data-rule-id="${id}"]`);
              if (card) card.remove();
            });
            updateBar();
          }
        })
        .catch(() => alert('Error deleting rules.'));
    });
  }

  // ── Single rule delete via row button ─────────────────────────────────────
  if (ruleList && deleteTpl) {
    ruleList.addEventListener('click', (e) => {
      const btn = e.target.closest('.rule-delete-btn');
      if (!btn) return;
      const pk = btn.dataset.pk;
      if (!pk || !confirm('Delete this rule?')) return;

      const url = deleteTpl.replace('__PK__', pk);
      fetch(url, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrf(),
          'X-Requested-With': 'XMLHttpRequest',
        },
      })
        .then(r => r.json())
        .then(data => {
          if (data.ok) {
            const card = ruleList.querySelector(`.rule-card[data-rule-id="${pk}"]`);
            if (card) card.remove();
          }
        })
        .catch(() => alert('Error deleting rule.'));
    });
  }

})();
