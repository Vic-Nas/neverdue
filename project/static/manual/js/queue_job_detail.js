(function () {
  var btn = document.getElementById('reprocess-btn');
  if (!btn) return;
  var CSRF = document.querySelector('meta[name="csrf-token"]').content;

  btn.addEventListener('click', function () {
    var prompt = document.getElementById('reprocess-prompt').value.trim();
    if (!prompt) { document.getElementById('reprocess-prompt').focus(); return; }

    window.neverdue.submitWithStatus({
      btn: btn,
      statusEl: document.getElementById('reprocess-status'),
      url: btn.dataset.reprocessUrl,
      body: { event_ids: JSON.parse(btn.dataset.pendingIds), prompt: prompt },
      csrf: CSRF,
      originalText: 'Re-process',
      successText: 'Queued. Updating this job\u2026',
      onSuccess: function () {
        setTimeout(function () { window.location.href = btn.dataset.queueUrl; }, 1200);
      },
    });
  });
})();
