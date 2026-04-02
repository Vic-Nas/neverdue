// project/static/manual/js/pages/queue_action.js
// Handles both the reprocess button (needs_review jobs) and the retry button
// (failed jobs) on queue_job_detail.html. Replaces queue_job_detail.js and
// queue_job_retry.js — loaded once, each section guards on element existence.
(function () {
  var CSRF = document.querySelector('meta[name="csrf-token"]').content;

  // Reprocess: needs_review job — user submits a correction prompt.
  var reprocessBtn = document.getElementById('reprocess-btn');
  if (reprocessBtn) {
    reprocessBtn.addEventListener('click', function () {
      var prompt = document.getElementById('reprocess-prompt').value.trim();
      if (!prompt) { document.getElementById('reprocess-prompt').focus(); return; }
      window.neverdue.submitWithStatus({
        btn:          reprocessBtn,
        statusEl:     document.getElementById('reprocess-status'),
        url:          reprocessBtn.dataset.reprocessUrl,
        body:         { event_ids: JSON.parse(reprocessBtn.dataset.pendingIds), prompt: prompt },
        csrf:         CSRF,
        originalText: 'Re-process',
        successText:  'Queued. Updating this job\u2026',
        onSuccess: function () {
          setTimeout(function () { window.location.href = reprocessBtn.dataset.queueUrl; }, 1200);
        },
      });
    });
  }

  // Retry: failed job — re-dispatch with original task_args.
  var retryBtn = document.getElementById('retry-btn');
  if (retryBtn) {
    retryBtn.addEventListener('click', function () {
      window.neverdue.submitWithStatus({
        btn:          retryBtn,
        statusEl:     document.getElementById('retry-status'),
        url:          retryBtn.dataset.retryUrl,
        body:         {},
        csrf:         CSRF,
        originalText: 'Retry job',
        successText:  'Job queued for retry.',
        onSuccess: function () {
          setTimeout(function () { window.location.href = retryBtn.dataset.queueUrl; }, 1200);
        },
      });
    });
  }
})();
