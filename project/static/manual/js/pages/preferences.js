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
      // Update selected ring
      group.querySelectorAll('.gcal-swatch').forEach(function (s) { s.classList.remove('selected'); });
      var label = group.querySelector('label[for="' + e.target.id + '"]');
      if (label) label.classList.add('selected');

      // Update the preview swatch in the row
      var row = group.closest('.priority-color-row');
      var preview = row && row.querySelector('.gcal-swatch-preview');
      if (preview && label) {
        preview.style.background = label.style.background;
        preview.title = label.title;
      }

      // Close the <details> after picking
      var details = group.closest('.gcal-picker-details');
      if (details) details.removeAttribute('open');
    });
  });

  // Revoke Google permissions button
  var revokeBtn = document.getElementById('revoke-google-btn');
  if (revokeBtn) {
    revokeBtn.addEventListener('click', function () {
      if (!confirm('This will disconnect your Google account and disable calendar sync. Continue?')) return;
      var url = revokeBtn.getAttribute('data-url');
      var csrfEl = document.querySelector('[name=csrfmiddlewaretoken]');
      var csrf = csrfEl ? csrfEl.value : '';
      fetch(url, { method: 'POST', headers: { 'X-CSRFToken': csrf } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            var cb = document.getElementById('save_to_gcal');
            if (cb) cb.checked = false;
            revokeBtn.textContent = 'Revoked';
            revokeBtn.disabled = true;
          } else {
            alert(data.error || 'Failed to revoke.');
          }
        })
        .catch(function () { alert('Network error. Try again.'); });
    });
  }
})();