(function () {
  var btn = document.getElementById('prompt-submit-btn');
  if (!btn) return;
  var CSRF = document.querySelector('meta[name="csrf-token"]').content;

  btn.addEventListener('click', function () {
    var prompt = document.getElementById('prompt-input').value.trim();
    if (!prompt) { document.getElementById('prompt-input').focus(); return; }

    window.neverdue.submitWithStatus({
      btn: btn,
      statusEl: null,
      url: btn.dataset.promptUrl,
      body: { prompt: prompt },
      csrf: CSRF,
      originalText: 'Apply',
      onSuccess: function () {
        window.location.href = btn.dataset.dashboardUrl;
      },
    });
  });
})();
