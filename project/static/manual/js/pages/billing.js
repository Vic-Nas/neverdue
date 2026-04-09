// project/static/manual/js/pages/billing.js
(function () {
  'use strict';

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;

  // ── Generate referral code ────────────────────────────────────────────────
  const generateBtn = document.getElementById('generate-code-btn');
  if (generateBtn) {
    generateBtn.addEventListener('click', async function () {
      generateBtn.disabled = true;
      generateBtn.textContent = 'Generating…';
      try {
        const res = await fetch('/billing/referral-code/generate/', {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken },
        });
        const data = await res.json();
        if (data.code) {
          // Replace button with code display
          const box = generateBtn.closest('.referral-code-box');
          box.innerHTML = `
            <span class="referral-code-box__label">Your referral code</span>
            <code class="referral-code-box__code" id="referral-code">${data.code}</code>
            <button class="btn-ghost btn btn--sm" onclick="copyCode()">Copy</button>
          `;
        } else {
          generateBtn.textContent = 'Error — try again';
          generateBtn.disabled = false;
        }
      } catch {
        generateBtn.textContent = 'Error — try again';
        generateBtn.disabled = false;
      }
    });
  }

  // ── Copy code to clipboard ────────────────────────────────────────────────
  window.copyCode = function () {
    const code = document.getElementById('referral-code')?.textContent?.trim();
    if (!code) return;
    navigator.clipboard.writeText(code).then(() => {
      const btn = document.getElementById('copy-code-btn');
      if (btn) {
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = orig; }, 1500);
      }
    });
  };
}());
