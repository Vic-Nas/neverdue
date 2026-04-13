/* js/pages/support.js — ticket detail resolve interaction */

(function () {
  'use strict';

  const resolveBlock = document.getElementById('resolve-block');
  if (!resolveBlock) return;

  const resolveUrl = resolveBlock.dataset.resolveUrl;
  const btnYes     = document.getElementById('btn-yes');
  const btnNo      = document.getElementById('btn-no');
  const msg        = document.getElementById('resolve-msg');

  function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : '';
  }

  function resolve(satisfied) {
    if (btnYes) btnYes.disabled = true;
    if (btnNo)  btnNo.disabled  = true;

    fetch(resolveUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf(),
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: JSON.stringify({ satisfied }),
    })
      .then(r => r.json())
      .then(data => {
        if (msg) {
          msg.hidden = false;
          msg.textContent = satisfied
            ? '✓ Marked as resolved. Thanks!'
            : 'Got it — we\'ll take another look.';
        }
        // Hide the action buttons
        const actions = resolveBlock.querySelector('.ticket-resolve__actions');
        if (actions) actions.hidden = true;
      })
      .catch(() => {
        if (btnYes) btnYes.disabled = false;
        if (btnNo)  btnNo.disabled  = false;
        if (msg) { msg.hidden = false; msg.textContent = 'Network error. Please try again.'; }
      });
  }

  if (btnYes) btnYes.addEventListener('click', () => resolve(true));
  if (btnNo)  btnNo.addEventListener('click',  () => resolve(false));

})();