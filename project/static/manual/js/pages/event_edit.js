// project/static/manual/js/pages/event_edit.js
(function () {
  // ── Dynamic rows ──────────────────────────────────────────────
  var addReminderBtn = document.getElementById('add-reminder-btn');
  if (addReminderBtn) {
    addReminderBtn.addEventListener('click', function () {
      addDynamicRow(
        'event-reminders-list',
        'reminder-row',
        '<input type="number" name="reminders" min="1" placeholder="Minutes before">' +
        '<span class="reminder-row__label">minutes before</span>' +
        '<button type="button" class="reminder-row__remove" onclick="this.parentElement.remove()">✕</button>'
      );
    });
  }

  var addLinkBtn = document.getElementById('add-link-btn');
  if (addLinkBtn) {
    addLinkBtn.addEventListener('click', function () {
      addDynamicRow(
        'event-links-list',
        'reminder-row',
        '<input type="url" name="link_urls" placeholder="https://…">' +
        '<input type="text" name="link_titles" placeholder="Label (optional)">' +
        '<button type="button" class="reminder-row__remove" onclick="this.parentElement.remove()">✕</button>'
      );
    });
  }

  // ── AI prompt edit (existing events only) ────────────────────
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