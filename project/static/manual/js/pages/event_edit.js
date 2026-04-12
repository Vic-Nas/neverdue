/* js/pages/event_edit.js — event edit/create form extras */

(function () {
  'use strict';

  function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : '';
  }

  // ── AI prompt edit ────────────────────────────────────────────────────────
  const submitBtn   = document.getElementById('prompt-submit-btn');
  const promptInput = document.getElementById('prompt-input');

  if (submitBtn && promptInput) {
    const promptUrl    = submitBtn.dataset.promptUrl;
    const dashboardUrl = submitBtn.dataset.dashboardUrl;

    submitBtn.addEventListener('click', () => {
      const prompt = promptInput.value.trim();
      if (!prompt) { promptInput.focus(); return; }

      submitBtn.disabled = true;
      submitBtn.textContent = 'Applying…';

      fetch(promptUrl, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrf(),
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({ prompt }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.ok) {
            window.location.href = dashboardUrl;
          } else {
            alert(data.error || 'Error applying prompt.');
            submitBtn.disabled = false;
            submitBtn.textContent = 'Apply';
          }
        })
        .catch(() => {
          alert('Network error. Please try again.');
          submitBtn.disabled = false;
          submitBtn.textContent = 'Apply';
        });
    });

    // Submit on Ctrl/Cmd+Enter
    promptInput.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') submitBtn.click();
    });
  }

})();
