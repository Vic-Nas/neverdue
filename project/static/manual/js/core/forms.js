// project/static/manual/js/core/forms.js

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
