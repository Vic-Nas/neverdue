(function () {
  var btn = document.getElementById('reprocess-btn');
  if (!btn) return;

  var REPROCESS_URL = btn.dataset.reprocessUrl;
  var CSRF = document.querySelector('meta[name="csrf-token"]').content;
  var PENDING_IDS = JSON.parse(btn.dataset.pendingIds);
  var QUEUE_URL = btn.dataset.queueUrl;

  btn.addEventListener('click', async function () {
    var prompt = document.getElementById('reprocess-prompt').value.trim();
    var status = document.getElementById('reprocess-status');
    if (!prompt) {
      document.getElementById('reprocess-prompt').focus();
      return;
    }
    this.disabled = true;
    this.textContent = 'Queuing…';
    status.textContent = '';

    try {
      var res = await fetch(REPROCESS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
        body: JSON.stringify({ event_ids: PENDING_IDS, prompt }),
      });
      var data = await res.json();
      if (data.ok) {
        status.textContent = 'Queued. Updating this job…';
        status.style.color = 'var(--color-success, #16a34a)';
        setTimeout(function () { window.location.href = QUEUE_URL; }, 1200);
      } else {
        status.textContent = 'Error: ' + data.error;
        status.style.color = '#ef4444';
        btn.disabled = false;
        btn.textContent = 'Re-process';
      }
    } catch (e) {
      status.textContent = 'Network error.';
      status.style.color = '#ef4444';
      btn.disabled = false;
      btn.textContent = 'Re-process';
    }
  });
})();
