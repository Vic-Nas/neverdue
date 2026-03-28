(function () {
  var meta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = meta ? meta.content : '';

  // ── Timezone auto-detect ──
  var timezoneUrl = document.body.dataset.timezoneUrl;
  if (timezoneUrl) {
    try {
      var tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (tz) {
        fetch(timezoneUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
          body: JSON.stringify({ timezone: tz })
        });
      }
    } catch (e) {}
  }

  // ── Mobile hamburger ──
  var hamburger = document.getElementById('nav-hamburger');
  var topnav = document.getElementById('topnav');
  var navLinks = document.getElementById('nav-links');

  if (hamburger) {
    hamburger.addEventListener('click', function () {
      var isOpen = topnav.classList.toggle('topnav--open');
      hamburger.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });

    navLinks.querySelectorAll('.topnav__link').forEach(function (link) {
      link.addEventListener('click', function () {
        topnav.classList.remove('topnav--open');
        hamburger.setAttribute('aria-expanded', 'false');
      });
    });

    document.addEventListener('click', function (e) {
      if (!topnav.contains(e.target)) {
        topnav.classList.remove('topnav--open');
        hamburger.setAttribute('aria-expanded', 'false');
      }
    });
  }

  // ── Queue badge polling ──
  var badgeProcessing = document.getElementById('queue-badge-processing');
  var badgeAttention = document.getElementById('queue-badge-attention');
  if (!badgeProcessing) return;

  var QUEUE_STATUS_URL = document.body.dataset.queueStatusUrl;
  var pollInterval = null;
  var POLL_MS = 5000;

  function updateBadges(activeCount, attentionCount) {
    if (activeCount > 0) {
      badgeProcessing.textContent = activeCount;
      badgeProcessing.hidden = false;
    } else {
      badgeProcessing.hidden = true;
    }
    if (attentionCount > 0) {
      badgeAttention.textContent = attentionCount;
      badgeAttention.hidden = false;
    } else {
      badgeAttention.hidden = true;
    }
  }

  function poll() {
    fetch(QUEUE_STATUS_URL, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        updateBadges(data.active_count, data.attention_count);
        if (data.active_count === 0 && pollInterval) {
          clearInterval(pollInterval);
          pollInterval = null;
        }
      })
      .catch(function () {});
  }

  function startPolling() {
    if (!pollInterval) {
      poll();
      pollInterval = setInterval(poll, POLL_MS);
    }
  }

  poll();

  document.addEventListener('submit', function () {
    setTimeout(startPolling, 1000);
  });

  window.neverdue = window.neverdue || {};
  window.neverdue.startQueuePolling = startPolling;
})();
