(function () {
  var autoDelete = document.getElementById('auto_delete');
  if (!autoDelete) return;

  var retentionGroup = document.getElementById('retention-group');
  var gcalGroup = document.getElementById('gcal-delete-group');

  function toggleCleanupFields() {
    var enabled = autoDelete.checked;
    retentionGroup.style.opacity = enabled ? '1' : '0.4';
    retentionGroup.style.pointerEvents = enabled ? '' : 'none';
    gcalGroup.style.opacity = enabled ? '1' : '0.4';
    gcalGroup.style.pointerEvents = enabled ? '' : 'none';
  }

  autoDelete.addEventListener('change', toggleCleanupFields);

  document.querySelectorAll('.gcal-swatches').forEach(function (group) {
    group.addEventListener('change', function (e) {
      group.querySelectorAll('.gcal-swatch').forEach(function (s) { s.classList.remove('selected'); });
      var label = group.querySelector('label[for="' + e.target.id + '"]');
      if (label) label.classList.add('selected');
    });
  });
})();
