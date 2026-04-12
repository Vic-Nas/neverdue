/* js/pages/billing.js — membership / referral page */

(function () {
  'use strict';

  function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : '';
  }

  // ── Copy referral code ────────────────────────────────────────────────────
  const copyBtn = document.getElementById('copy-code-btn');
  const codeEl  = document.getElementById('referral-code');

  if (copyBtn && codeEl) {
    copyBtn.addEventListener('click', () => {
      window.copyText ? window.copyText(codeEl.textContent.trim(), copyBtn)
        : navigator.clipboard.writeText(codeEl.textContent.trim()).then(() => {
            const orig = copyBtn.textContent;
            copyBtn.textContent = 'Copied!';
            setTimeout(() => { copyBtn.textContent = orig; }, 1800);
          });
    });
  }

  // Inline copyCode() called from onclick attribute in template
  window.copyCode = function () {
    if (copyBtn && codeEl) copyBtn.click();
  };

  // ── Generate referral code ────────────────────────────────────────────────
  const genBtn = document.getElementById('generate-code-btn');
  if (genBtn) {
    genBtn.addEventListener('click', () => {
      genBtn.disabled = true;
      genBtn.textContent = 'Generating…';

      fetch(window.location.pathname + 'generate-code/', {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrf(),
          'X-Requested-With': 'XMLHttpRequest',
        },
      })
        .then(r => r.json())
        .then(data => {
          if (data.code) {
            // Replace button with code display
            const box = genBtn.closest('.referral-code-box');
            if (box) {
              box.innerHTML = `
                <span class="referral-code-box__label">Your referral code</span>
                <code class="referral-code-box__code" id="referral-code">${data.code}</code>
                <button class="btn-ghost btn btn--sm" id="copy-code-btn">Copy</button>
              `;
              const newCopy = box.querySelector('#copy-code-btn');
              const newCode = box.querySelector('#referral-code');
              if (newCopy && newCode) {
                newCopy.addEventListener('click', () => {
                  window.copyText ? window.copyText(newCode.textContent.trim(), newCopy)
                    : navigator.clipboard.writeText(newCode.textContent.trim());
                });
              }
            }
          } else {
            genBtn.disabled = false;
            genBtn.textContent = 'Get my referral code';
            alert(data.error || 'Something went wrong. Please try again.');
          }
        })
        .catch(() => {
          genBtn.disabled = false;
          genBtn.textContent = 'Get my referral code';
          alert('Network error. Please try again.');
        });
    });
  }

  // ── Referral code lookup ──────────────────────────────────────────────────
  const lookupBtn    = document.getElementById('lookup-btn');
  const lookupInput  = document.getElementById('lookup-input');
  const lookupResult = document.getElementById('lookup-result');

  if (lookupBtn && lookupInput && lookupResult) {
    function doLookup() {
      const code = lookupInput.value.trim();
      if (!code) return;

      lookupBtn.disabled = true;
      lookupBtn.textContent = '…';
      lookupResult.hidden = true;

      fetch(`/billing/lookup-code/?code=${encodeURIComponent(code)}`, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      })
        .then(r => r.json())
        .then(data => {
          lookupResult.hidden = false;
          lookupResult.textContent = data.message || (data.valid ? 'Code is valid.' : 'Code not found.');
        })
        .catch(() => {
          lookupResult.hidden = false;
          lookupResult.textContent = 'Network error. Please try again.';
        })
        .finally(() => {
          lookupBtn.disabled = false;
          lookupBtn.textContent = 'Search';
        });
    }

    lookupBtn.addEventListener('click', doLookup);
    lookupInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') doLookup(); });
  }

})();
