// project/static/manual/js/pages/billing.js

// ── Referral code generation ──────────────────────────────────────────────────

const generateBtn = document.getElementById('generate-code-btn');
if (generateBtn) {
  generateBtn.addEventListener('click', () => {
    generateBtn.disabled = true;
    generateBtn.textContent = 'Generating…';

    fetch('/billing/referral-code/generate/', {
      method: 'POST',
      headers: { 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content },
    })
      .then(r => r.json())
      .then(data => {
        if (data.code) {
          const box = generateBtn.closest('.referral-code-box');
          box.innerHTML = `
            <span class="referral-code-box__label">Your referral code</span>
            <code class="referral-code-box__code" id="referral-code">${data.code}</code>
            <button class="btn-ghost btn btn--sm" onclick="copyCode()">Copy</button>
          `;
        } else {
          generateBtn.disabled = false;
          generateBtn.textContent = 'Get my referral code';
          alert(data.error || 'Could not generate code.');
        }
      })
      .catch(() => {
        generateBtn.disabled = false;
        generateBtn.textContent = 'Get my referral code';
        alert('Network error. Please try again.');
      });
  });
}

function copyCode() {
  const code = document.getElementById('referral-code');
  if (!code) return;
  navigator.clipboard.writeText(code.textContent.trim()).then(() => {
    const btn = document.getElementById('copy-code-btn');
    if (btn) { btn.textContent = 'Copied!'; setTimeout(() => { btn.textContent = 'Copy'; }, 2000); }
  });
}

// ── Referral code lookup ──────────────────────────────────────────────────────

const lookupInput = document.getElementById('lookup-input');
const lookupBtn   = document.getElementById('lookup-btn');
const lookupResult = document.getElementById('lookup-result');

function doLookup() {
  const code = lookupInput.value.trim().toUpperCase();
  if (!code) return;

  lookupBtn.disabled = true;
  lookupBtn.textContent = 'Searching…';
  lookupResult.hidden = true;
  lookupResult.className = 'referral-lookup__result';

  fetch(`/billing/referral/lookup/?code=${encodeURIComponent(code)}`)
    .then(r => r.json())
    .then(data => {
      lookupBtn.disabled = false;
      lookupBtn.textContent = 'Search';
      lookupResult.hidden = false;

      if (data.error) {
        lookupResult.classList.add('referral-lookup__result--error');
        lookupResult.textContent = data.error;
        return;
      }

      const headStatus = data.head_active
        ? `<span class="lookup-ok">Active</span>`
        : `<span class="lookup-warn">Inactive</span>`;

      lookupResult.classList.add('referral-lookup__result--ok');
      lookupResult.innerHTML = `
        <strong>${data.code}</strong> —
        referred by <strong>${data.head_label}</strong> (${headStatus}),
        <strong>${data.redeemer_count}</strong> active redeemer${data.redeemer_count !== 1 ? 's' : ''}.
      `;
    })
    .catch(() => {
      lookupBtn.disabled = false;
      lookupBtn.textContent = 'Search';
      lookupResult.hidden = false;
      lookupResult.classList.add('referral-lookup__result--error');
      lookupResult.textContent = 'Network error. Please try again.';
    });
}

if (lookupBtn) {
  lookupBtn.addEventListener('click', doLookup);
}
if (lookupInput) {
  lookupInput.addEventListener('keydown', e => { if (e.key === 'Enter') doLookup(); });
}
