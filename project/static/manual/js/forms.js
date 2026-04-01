/* static/manual/js/forms.js */

/**
 * Toggle a form group's disabled appearance based on a select value.
 * @param {HTMLSelectElement} selectEl  - the select that drives the toggle
 * @param {string} groupId              - id of the group div to enable/disable
 * @param {string|null} clearId         - optional input id to clear when disabling
 */
function toggleDependentGroup(selectEl, groupId, clearId) {
  const group = document.getElementById(groupId);
  if (!group) return;
  if (selectEl.value) {
    group.classList.remove('form-group--disabled');
  } else {
    group.classList.add('form-group--disabled');
    if (clearId) {
      const input = document.getElementById(clearId);
      if (input) input.value = '';
    }
  }
}

/**
 * Append a new dynamic row to a list container.
 * @param {string} listId     - id of the container element
 * @param {string} className  - CSS class for the new row div
 * @param {string} innerHTML  - inner HTML template for the row
 */
function addDynamicRow(listId, className, innerHTML) {
  const list = document.getElementById(listId);
  if (!list) return;
  const row = document.createElement('div');
  row.className = className;
  row.innerHTML = innerHTML;
  list.appendChild(row);
  const firstInput = row.querySelector('input, select, textarea');
  if (firstInput) firstInput.focus();
}

/* ── Event edit: recurrence toggle ── */
(function () {
  const freqSelect = document.getElementById('recurrence_freq');
  if (!freqSelect) return;

  freqSelect.addEventListener('change', function () {
    toggleDependentGroup(this, 'recurrence-until-group', 'recurrence_until');
  });
}());

/* ── Category edit: color label sync ── */
(function () {
  const colorInput = document.getElementById('color');
  if (!colorInput) return;

  colorInput.addEventListener('input', function () {
    const label = document.getElementById('color-label');
    if (label) label.textContent = this.value;
  });
}());

/* ── Category edit: add reminder row ── */
function addReminder() {
  addDynamicRow(
    'reminders-list',
    'reminder-row',
    `<input type="number" name="reminders" min="1" placeholder="Minutes before">
     <span class="reminder-row__label">minutes before</span>
     <button type="button" class="reminder-row__remove" onclick="this.parentElement.remove()">✕</button>`
  );
}

/* ── Category edit: add rule row ── */
function addRule() {
  addDynamicRow(
    'rules-list',
    'rule-row',
    `<input type="text" name="rule_sender" placeholder="Sender (e.g. prof@uni.ca)">
     <span class="rule-row__sep">or contains</span>
     <input type="text" name="rule_keyword" placeholder="Keyword (e.g. deadline)">
     <button type="button" class="reminder-row__remove" onclick="this.parentElement.remove()">✕</button>`
  );
}

/* ── Category edit: form submission ── */
(function () {
  const form = document.querySelector('.category-form');
  if (!form) return;

  form.addEventListener('submit', function (e) {
    e.preventDefault();

    const data = {
      name: document.getElementById('name').value,
      priority: parseInt(document.getElementById('priority').value) || 2,
      gcal_color_id: document.querySelector('input[name="gcal_color_id"]:checked')?.value || '',
      color: document.getElementById('color')?.value || '',
      reminders: Array.from(document.querySelectorAll('input[name="reminders"]')).map(el => parseInt(el.value) || 0).filter(v => v > 0),
    };

    fetch(form.action || window.location.href, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    .then(res => res.json())
    .then(result => {
      if (result.ok) {
        window.location.href = result.redirect || '/dashboard/categories/';
      } else {
        alert('Error: ' + (result.error || 'Unknown error'));
      }
    })
    .catch(err => {
      console.error('Error:', err);
      alert('Error: ' + err.message);
    });
  });
}());
