// project/static/manual/js/queue_job_retry.js
(function () {
  var btn = document.getElementById('retry-btn');
  if (!btn) return;

  var RETRY_URL = btn.dataset.retryUrl;
  var QUEUE_URL = btn.dataset.queueUrl;
  var CSRF = document.querySelector('meta[name="csrf-token"]').content;

  btn.addEventListener('click', async function () {
    var status = document.getElementById('retry-status');
    this.disabled = true;
    this.textContent = 'Queuing…';
    status.textContent = '';

    try {
      var res = await fetch(RETRY_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
        body: JSON.stringify({}),
      });
      var data = await res.json();
      if (data.ok) {
        status.textContent = 'Job queued for retry.';
        status.style.color = 'var(--color-success, #16a34a)';
        setTimeout(function () { window.location.href = QUEUE_URL; }, 1200);
      } else {
        status.textContent = 'Error: ' + data.error;
        status.style.color = '#ef4444';
        btn.disabled = false;
        btn.textContent = 'Retry job';
      }
    } catch (e) {
      status.textContent = 'Network error.';
      status.style.color = '#ef4444';
      btn.disabled = false;
      btn.textContent = 'Retry job';
    }
  });
})();
