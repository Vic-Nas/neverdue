(function () {
  var btn = document.getElementById('prompt-submit-btn');
  if (!btn) return;

  var PROMPT_URL = btn.dataset.promptUrl;
  var CSRF = document.querySelector('meta[name="csrf-token"]').content;
  var DASHBOARD_URL = btn.dataset.dashboardUrl;

  btn.addEventListener('click', async function () {
    var prompt = document.getElementById('prompt-input').value.trim();
    if (!prompt) {
      document.getElementById('prompt-input').focus();
      return;
    }
    btn.disabled = true;
    btn.textContent = 'Queuing…';

    try {
      var res = await fetch(PROMPT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
        body: JSON.stringify({ prompt }),
      });
      var data = await res.json();
      if (data.ok) {
        window.location.href = DASHBOARD_URL;
      } else {
        alert('Error: ' + data.error);
        btn.disabled = false;
        btn.textContent = 'Apply';
      }
    } catch (e) {
      alert('Network error. Please try again.');
      btn.disabled = false;
      btn.textContent = 'Apply';
    }
  });
})();
