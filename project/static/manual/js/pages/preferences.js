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

  // Force all color picker <details> open — swatches are always visible
  document.querySelectorAll('.gcal-picker-details').forEach(function (d) {
    d.setAttribute('open', '');
  });

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
// ── Live username validation ──
(function () {
  var input  = document.getElementById('new-username');
  var btn    = document.getElementById('username-change-btn');
  var err    = document.getElementById('username-change-error');
  var ok     = document.getElementById('username-change-ok');
  if (!input || !btn) return;

  var VALID_RE = /^[a-z0-9_]+$/;
  var MIN_LEN  = 3;

  function validate(val) {
    if (!val)                        return null; // empty — neutral, button disabled
    if (val.length < MIN_LEN)        return 'Too short — minimum ' + MIN_LEN + ' characters.';
    if (!VALID_RE.test(val))         return 'Only lowercase letters, numbers, and underscores allowed.';
    return '';                                    // valid
  }

  function setValidationUI(val) {
    var result = validate(val);
    ok.hidden  = true;
    if (result === null) {
      // Empty input — reset to neutral
      err.hidden = true;
      input.classList.remove('input--valid', 'input--invalid');
      btn.disabled = true;
    } else if (result === '') {
      // Valid
      err.hidden = true;
      input.classList.add('input--valid');
      input.classList.remove('input--invalid');
      btn.disabled = false;
    } else {
      // Invalid
      err.textContent = result;
      err.hidden = false;
      input.classList.add('input--invalid');
      input.classList.remove('input--valid');
      btn.disabled = true;
    }
  }

  // Start disabled
  btn.disabled = true;

  input.addEventListener('input', function () {
    setValidationUI(this.value.trim().toLowerCase());
  });
}());