// project/static/manual/js/queue_job_retry.js
(function () {
  var btn = document.getElementById('retry-btn');
  if (!btn) return;
  var CSRF = document.querySelector('meta[name="csrf-token"]').content;

  btn.addEventListener('click', function () {
    window.neverdue.submitWithStatus({
      btn: btn,
      statusEl: document.getElementById('retry-status'),
      url: btn.dataset.retryUrl,
      body: {},
      csrf: CSRF,
      originalText: 'Retry job',
      successText: 'Job queued for retry.',
      onSuccess: function () {
        setTimeout(function () { window.location.href = btn.dataset.queueUrl; }, 1200);
      },
    });
  });
})();
