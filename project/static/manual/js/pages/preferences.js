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
})();