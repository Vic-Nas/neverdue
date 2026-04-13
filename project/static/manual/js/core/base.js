/* js/core/base.js — global JS loaded for authenticated users */

(function () {
  'use strict';

  // ── Theme toggle (dark / light) ──────────────────────────────────────────
  const themeToggle = document.getElementById('theme-toggle');
  const html = document.documentElement;

  function applyTheme(theme) {
    html.setAttribute('data-theme', theme);
    if (themeToggle) {
      themeToggle.textContent = theme === 'dark' ? '☀️' : '🌙';
      themeToggle.setAttribute('aria-label', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
    }
    try { localStorage.setItem('nd-theme', theme); } catch(_) {}
  }

  // Set initial icon based on current theme
  applyTheme(html.getAttribute('data-theme') || 'dark');

  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      const current = html.getAttribute('data-theme') || 'dark';
      applyTheme(current === 'dark' ? 'light' : 'dark');
    });
  }

  // ── Hamburger nav ────────────────────────────────────────────────────────
  const hamburger = document.getElementById('nav-hamburger');
  const navLinks  = document.getElementById('nav-links');
  if (hamburger && navLinks) {
    hamburger.addEventListener('click', () => {
      const open = navLinks.classList.toggle('is-open');
      hamburger.setAttribute('aria-expanded', String(open));
    });
    // Close on outside click
    document.addEventListener('click', (e) => {
      if (!hamburger.contains(e.target) && !navLinks.contains(e.target)) {
        navLinks.classList.remove('is-open');
        hamburger.setAttribute('aria-expanded', 'false');
      }
    });
  }

  // ── Queue badge polling ───────────────────────────────────────────────────
  const body = document.body;
  const queueStatusUrl = body.dataset.queueStatusUrl;
  const badgeProcessing = document.getElementById('queue-badge-processing');
  const badgeAttention  = document.getElementById('queue-badge-attention');

  if (queueStatusUrl && (badgeProcessing || badgeAttention)) {
    let pollInterval = 8000;
    let timerId;

    function poll() {
      fetch(queueStatusUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (!data) return;
          if (badgeProcessing) badgeProcessing.hidden = !data.processing;
          if (badgeAttention)  badgeAttention.hidden  = !data.attention;
          // Back off when nothing is in flight
          pollInterval = (data.processing) ? 5000 : 12000;
        })
        .catch(() => { pollInterval = 20000; })
        .finally(() => { timerId = setTimeout(poll, pollInterval); });
    }

    timerId = setTimeout(poll, 3000);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        clearTimeout(timerId);
      } else {
        timerId = setTimeout(poll, 1000);
      }
    });
  }

  // ── Timezone auto-detect ─────────────────────────────────────────────────
  const tzUrl = body.dataset.timezoneUrl;
  if (tzUrl) {
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (tz) {
        const fd = new FormData();
        fd.append('timezone', tz);
        fd.append('csrfmiddlewaretoken', csrfToken());
        fetch(tzUrl, { method: 'POST', body: fd }).catch(() => {});
      }
    } catch (_) {}
  }

  // ── Shared helper: CSRF token ─────────────────────────────────────────────
  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
  }
  // Expose globally for inline scripts (e.g. preferences page)
  window.csrfToken = csrfToken;

  // ── Shared helper: copy text ──────────────────────────────────────────────
  window.copyText = function (text, btn) {
    navigator.clipboard.writeText(text).then(() => {
      if (!btn) return;
      const orig = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = orig; }, 1800);
    }).catch(() => {
      // Fallback for older browsers
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;opacity:0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    });
  };

})();
