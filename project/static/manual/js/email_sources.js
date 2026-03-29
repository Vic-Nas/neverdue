/* static/manual/js/email_sources.js */

var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

async function addFilterRule() {
  const action  = document.getElementById('filter-action').value;
  const pattern = document.getElementById('filter-pattern').value.trim();
  if (!pattern) return;

  const addRuleUrl = document.getElementById('filter-add-url').value;
  const resp = await fetch(addRuleUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': CSRF,
    },
    body: JSON.stringify({ action, pattern }),
  });
  const data = await resp.json();

  if (data.ok) {
    document.getElementById('filter-empty')?.remove();
    const list = document.getElementById('filter-rules-list');
    const div  = document.createElement('div');
    div.className  = `filter-rule filter-rule--${action}`;
    div.dataset.id = data.id;
    div.innerHTML  = `
      <span class="filter-rule__badge filter-rule__badge--${action}">${action}</span>
      <span class="filter-rule__pattern">${pattern}</span>
      <button class="filter-rule__remove btn-ghost btn btn--sm"
              onclick="removeFilterRule(${data.id})">✕</button>
    `;
    list.appendChild(div);
    document.getElementById('filter-pattern').value = '';
  } else {
    alert(data.error || 'Could not add rule.');
  }
}

async function removeFilterRule(id) {
  const deleteUrlTemplate = document.getElementById('filter-delete-url-template').value;
  const url = deleteUrlTemplate.replace('/0/', `/${id}/`);
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'X-CSRFToken': CSRF },
  });
  const data = await resp.json();

  if (data.ok) {
    document.querySelector(`[data-id="${id}"]`).remove();
    if (!document.querySelectorAll('.filter-rule').length) {
      const p    = document.createElement('p');
      p.className = 'filter-rules__empty';
      p.id        = 'filter-empty';
      p.textContent = 'All senders allowed — no filters active.';
      document.getElementById('filter-rules-list').appendChild(p);
    }
  }
}

function copyInboxAddress() {
  const addr = document.getElementById('inbox-address').textContent;
  navigator.clipboard.writeText(addr).then(() => {
    const btn = event.target;
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy', 2000);
  });
}
