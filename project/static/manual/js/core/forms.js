/* js/core/forms.js — dynamic form helpers (reminders, links, recurrence) */

(function () {
  'use strict';

  // ── Add reminder row ──────────────────────────────────────────────────────
  const addReminderBtn = document.getElementById('add-reminder-btn');
  const remindersList  = document.getElementById('event-reminders-list');

  if (addReminderBtn && remindersList) {
    addReminderBtn.addEventListener('click', () => {
      const row = document.createElement('div');
      row.className = 'reminder-row';
      row.innerHTML = `
        <input type="number" name="reminders" min="1" placeholder="Minutes before">
        <span class="reminder-row__label">minutes before</span>
        <button type="button" class="reminder-row__remove" onclick="this.parentElement.remove()">✕</button>
      `;
      remindersList.appendChild(row);
      row.querySelector('input').focus();
    });
  }

  // ── Add link row ──────────────────────────────────────────────────────────
  const addLinkBtn  = document.getElementById('add-link-btn');
  const linksList   = document.getElementById('event-links-list');

  if (addLinkBtn && linksList) {
    addLinkBtn.addEventListener('click', () => {
      const row = document.createElement('div');
      row.className = 'reminder-row';
      row.innerHTML = `
        <input type="url" name="link_urls" placeholder="https://…">
        <input type="text" name="link_titles" placeholder="Label (optional)">
        <button type="button" class="reminder-row__remove" onclick="this.parentElement.remove()">✕</button>
      `;
      linksList.appendChild(row);
      row.querySelector('input').focus();
    });
  }

  // ── Recurrence until field enable/disable ─────────────────────────────────
  const freqSelect    = document.getElementById('recurrence_freq');
  const untilGroup    = document.getElementById('recurrence-until-group');

  if (freqSelect && untilGroup) {
    function updateUntil() {
      if (freqSelect.value) {
        untilGroup.classList.remove('form-group--disabled');
      } else {
        untilGroup.classList.add('form-group--disabled');
        const untilInput = untilGroup.querySelector('input');
        if (untilInput) untilInput.value = '';
      }
    }
    freqSelect.addEventListener('change', updateUntil);
    updateUntil();
  }

})();
