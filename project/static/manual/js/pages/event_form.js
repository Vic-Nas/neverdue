// project/static/manual/js/pages/event_form.js
// Intercepts the event-form submit and sends JSON to the backend.
(function () {
  var form = document.querySelector('.event-form');
  if (!form) return;
  var CSRF = document.querySelector('meta[name="csrf-token"]').content;

  form.addEventListener('submit', function (e) {
    e.preventDefault();

    var title = form.querySelector('#title').value.trim();
    var start = form.querySelector('#start').value;
    var end = form.querySelector('#end').value;
    var description = (form.querySelector('#description').value || '').trim();
    var categorySelect = form.querySelector('#category');
    var categoryId = categorySelect ? categorySelect.value : '';

    // Color: whichever radio is checked
    var colorInput = form.querySelector('input[name="color"]:checked');
    var color = colorInput ? colorInput.value : '';

    // Recurrence
    var freqSelect = form.querySelector('#recurrence_freq');
    var recurrenceFreq = freqSelect ? freqSelect.value : '';
    var recurrenceUntil = form.querySelector('#recurrence_until')
      ? form.querySelector('#recurrence_until').value : '';

    // Reminders: collect all number inputs named "reminders"
    var reminderInputs = form.querySelectorAll('input[name="reminders"]');
    var reminders = [];
    reminderInputs.forEach(function (inp) {
      var v = parseInt(inp.value, 10);
      if (v > 0) reminders.push(v);
    });

    if (!title || !start || !end) {
      alert('Title, start, and end are required.');
      return;
    }

    var body = {
      title: title,
      start: start,
      end: end,
      description: description,
      category_id: categoryId || null,
      color: color,
      recurrence_freq: recurrenceFreq || null,
      recurrence_until: recurrenceUntil || null,
      reminders: reminders,
    };

    var submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Saving…';
    }

    fetch(form.action || window.location.href, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify(body),
      credentials: 'same-origin',
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          // Redirect to event detail or dashboard
          window.location.href = data.pk
            ? '/dashboard/events/' + data.pk + '/'
            : '/dashboard/';
        } else {
          alert('Error: ' + (data.error || 'Unknown error'));
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = submitBtn.dataset.originalText || 'Save';
          }
        }
      })
      .catch(function () {
        alert('Network error. Please try again.');
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = submitBtn.dataset.originalText || 'Save';
        }
      });
  });
})();
