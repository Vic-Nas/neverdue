/* js/pages/preferences.js — preferences form interactions */

(function () {
  'use strict';

  // ── Auto-delete sub-fields toggle ─────────────────────────────────────────
  const autoDeleteCb  = document.getElementById('auto_delete');
  const subFields     = document.getElementById('cleanup-sub-fields');

  if (autoDeleteCb && subFields) {
    function syncSubFields() {
      if (autoDeleteCb.checked) {
        subFields.style.opacity = '';
        subFields.style.pointerEvents = '';
      } else {
        subFields.style.opacity = '0.4';
        subFields.style.pointerEvents = 'none';
      }
    }
    autoDeleteCb.addEventListener('change', syncSubFields);
    syncSubFields();
  }

  // ── GCal color swatch picker — close <details> after picking ─────────────
  document.querySelectorAll('.gcal-swatches[data-field]').forEach(swatchGroup => {
    swatchGroup.addEventListener('change', (e) => {
      if (e.target.type === 'radio') {
        const details = swatchGroup.closest('details');
        if (details) details.open = false;

        // Update preview swatch
        const row = swatchGroup.closest('.priority-color-row');
        if (row) {
          const preview = row.querySelector('.gcal-swatch-preview');
          if (preview) {
            // Find the selected label's background color
            const lbl = swatchGroup.querySelector(`label[for="${e.target.id}"]`);
            if (lbl) preview.style.background = lbl.style.background;
          }
        }
      }
    });
  });

  // ── Google revoke/restore button ──────────────────────────────────────────
  const googleBtn  = document.getElementById('google-permissions-btn');
  const googleHint = document.getElementById('google-permissions-hint');

  if (googleBtn && googleBtn.dataset.action === 'revoke') {
    googleBtn.addEventListener('click', () => {
      if (!confirm('Revoke Google permissions? Calendar sync will stop working until you reconnect.')) return;
      const revokeUrl = googleBtn.dataset.revokeUrl;
      const restoreUrl = googleBtn.dataset.restoreUrl;
      const csrf = window.csrfToken ? window.csrfToken() : '';

      googleBtn.disabled = true;
      googleBtn.textContent = 'Revoking…';

      const fd = new FormData();
      fd.append('csrfmiddlewaretoken', csrf);
      fetch(revokeUrl, { method: 'POST', body: fd })
        .then(r => r.json())
        .then(data => {
          if (data.ok) {
            googleBtn.outerHTML = `<a href="${restoreUrl}" class="btn btn-primary" id="google-permissions-btn" data-action="restore">Restore Google permissions</a>`;
            if (googleHint) googleHint.textContent = 'Reconnect your Google account to enable calendar sync.';
          } else {
            googleBtn.disabled = false;
            googleBtn.textContent = 'Revoke Google permissions';
            alert(data.error || 'Failed to revoke permissions.');
          }
        })
        .catch(() => {
          googleBtn.disabled = false;
          googleBtn.textContent = 'Revoke Google permissions';
          alert('Network error.');
        });
    });
  }

})();
