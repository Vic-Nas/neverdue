// project/static/manual/js/pages/support.js
(function () {
  const block  = document.getElementById('resolve-block');
  if (!block) return;

  const btnYes = document.getElementById('btn-yes');
  const btnNo  = document.getElementById('btn-no');
  const msg    = document.getElementById('resolve-msg');
  const url    = block.dataset.resolveUrl;
  const csrf   = document.querySelector('meta[name="csrf-token"]').content;

  async function resolve(satisfied) {
    btnYes.disabled = true;
    btnNo.disabled  = true;

    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({ satisfied }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      msg.textContent = data.error || 'Something went wrong.';
      msg.hidden = false;
      btnYes.disabled = false;
      btnNo.disabled  = false;
      return;
    }

    block.innerHTML = satisfied
      ? '<p class="support-notice">✓ Glad it helped! Ticket closed.</p>'
      : `<p class="support-notice">Issue opened on GitHub. <a href="${data.gh_url}" target="_blank" rel="noopener">View it here →</a></p>`;
  }

  btnYes.addEventListener('click', () => resolve(true));
  btnNo.addEventListener('click',  () => resolve(false));
}());