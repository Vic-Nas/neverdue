/* js/pages/queue_action.js — queue job detail page actions */

(function () {
  'use strict';

  function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : '';
  }

  function postAction(url, body, onOk, statusEl) {
    return fetch(url, {
      method: 'POST',
      headers: {
        'X-CSRFToken': csrf(),
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: JSON.stringify(body || {}),
    })
      .then(r => r.json())
      .then(data => {
        if (data.ok) onOk(data);
        else if (statusEl) statusEl.textContent = data.error || 'Error.';
      })
      .catch(() => { if (statusEl) statusEl.textContent = 'Network error.'; });
  }

  // ── Retry ─────────────────────────────────────────────────────────────────
  const retryBtn    = document.getElementById('retry-btn');
  const retryStatus = document.getElementById('retry-status');

  if (retryBtn) {
    retryBtn.addEventListener('click', () => {
      retryBtn.disabled = true;
      retryBtn.textContent = 'Retrying…';
      postAction(
        retryBtn.dataset.retryUrl, {},
        () => { window.location.href = retryBtn.dataset.queueUrl; },
        retryStatus
      ).finally(() => {
        if (retryBtn.disabled) { retryBtn.disabled = false; retryBtn.textContent = 'Retry job'; }
      });
    });
  }

  // ── Reprocess pending events ──────────────────────────────────────────────
  const reprocessBtn    = document.getElementById('reprocess-btn');
  const reprocessStatus = document.getElementById('reprocess-status');
  const reprocessPrompt = document.getElementById('reprocess-prompt');

  if (reprocessBtn) {
    reprocessBtn.addEventListener('click', () => {
      const prompt = reprocessPrompt ? reprocessPrompt.value.trim() : '';
      const ids    = JSON.parse(reprocessBtn.dataset.pendingIds || '[]');

      reprocessBtn.disabled = true;
      reprocessBtn.textContent = 'Sending…';
      postAction(
        reprocessBtn.dataset.reprocessUrl,
        { prompt, pending_ids: ids },
        () => { window.location.href = reprocessBtn.dataset.queueUrl; },
        reprocessStatus
      ).finally(() => {
        if (reprocessBtn.disabled) { reprocessBtn.disabled = false; reprocessBtn.textContent = 'Re-process'; }
      });
    });
    if (reprocessPrompt) {
      reprocessPrompt.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') reprocessBtn.click();
      });
    }
  }

  // ── Delete job ────────────────────────────────────────────────────────────
  const deleteJobBtn = document.getElementById('delete-job-btn');
  const deleteStatus = document.getElementById('delete-status');

  if (deleteJobBtn) {
    deleteJobBtn.addEventListener('click', () => {
      if (!confirm('Delete this job permanently?')) return;
      deleteJobBtn.disabled = true;
      deleteJobBtn.textContent = 'Deleting…';
      postAction(
        deleteJobBtn.dataset.deleteUrl, {},
        () => { window.location.href = deleteJobBtn.dataset.queueUrl; },
        deleteStatus
      ).finally(() => {
        if (deleteJobBtn.disabled) { deleteJobBtn.disabled = false; deleteJobBtn.textContent = 'Delete job'; }
      });
    });
  }

})();
