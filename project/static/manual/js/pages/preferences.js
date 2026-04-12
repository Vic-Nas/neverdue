// project/static/manual/js/pages/preferences.js
(function () {
  var autoDelete = document.getElementById('auto_delete');
  if (!autoDelete) return;

  var cleanupSub = document.getElementById('cleanup-sub-fields');

  function toggleCleanupFields() {
    var enabled = autoDelete.checked;
    cleanupSub.style.opacity = enabled ? '1' : '0.4';
    cleanupSub.style.pointerEvents = enabled ? '' : 'none';
  }

  autoDelete.addEventListener('change', toggleCleanupFields);

  document.querySelectorAll('.gcal-swatches').forEach(function (group) {
    group.addEventListener('change', function (e) {
      group.querySelectorAll('.gcal-swatch').forEach(function (s) { s.classList.remove('selected'); });
      var label = group.querySelector('label[for="' + e.target.id + '"]');
      if (label) label.classList.add('selected');
    });
  });

  // Google permissions button (Revoke / Restore)
  var googleBtn = document.getElementById('google-permissions-btn');
  if (googleBtn && googleBtn.getAttribute('data-action') === 'revoke') {
    googleBtn.addEventListener('click', function () {
      if (!confirm('This will disconnect your Google account and disable calendar sync. Continue?')) return;
      var url = googleBtn.getAttribute('data-revoke-url');
      var csrfEl = document.querySelector('[name=csrfmiddlewaretoken]');
      var csrf = csrfEl ? csrfEl.value : '';
      fetch(url, { method: 'POST', headers: { 'X-CSRFToken': csrf } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            var cb = document.getElementById('save_to_gcal');
            if (cb) cb.checked = false;
            googleBtn.textContent = 'Restore Google permissions';
            googleBtn.classList.remove('btn-danger');
            googleBtn.classList.add('btn-primary');
            googleBtn.setAttribute('data-action', 'restore');
            googleBtn.disabled = false;
            // Switch to a link for restore
            googleBtn.addEventListener('click', function () {
              window.location.href = googleBtn.getAttribute('data-restore-url');
            }, { once: true });
            var hint = document.getElementById('google-permissions-hint');
            if (hint) hint.textContent = 'Reconnect your Google account to enable calendar sync.';
          } else {
            alert(data.error || 'Failed to revoke.');
          }
        })
        .catch(function () { alert('Network error. Try again.'); });
    });
  }
})();