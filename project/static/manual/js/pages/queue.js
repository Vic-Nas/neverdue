/* js/pages/queue.js — processing queue list with live polling */

(function () {
  'use strict';

  function csrf() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : '';
  }

  const tbody       = document.getElementById('queue-tbody');
  const tableEl     = document.getElementById('queue-table');
  const emptyMsg    = document.getElementById('queue-empty-msg');
  const searchInput = document.getElementById('queue-search');
  const filterSel   = document.getElementById('queue-filter-status');

  let allJobs = [];
  let pollTimer;

  // ── Status badge map ──────────────────────────────────────────────────────
  function statusBadge(status) {
    const labels = {
      queued: 'Queued', processing: 'Processing',
      done: 'Done', needs_review: 'Needs review', failed: 'Failed',
    };
    return `<span class="badge badge--${status}">${labels[status] || status}</span>`;
  }

  function sourceLabel(src) {
    return { email: 'Email', upload: 'Upload', manual: 'Manual', api: 'API' }[src] || src;
  }

  // ── Render ────────────────────────────────────────────────────────────────
  function render() {
    if (!tbody) return;
    const q      = searchInput ? searchInput.value.toLowerCase() : '';
    const status = filterSel   ? filterSel.value : '';

    const visible = allJobs.filter(j => {
      if (status && j.status !== status) return false;
      if (q && !JSON.stringify(j).toLowerCase().includes(q)) return false;
      return true;
    });

    if (!visible.length) {
      if (tableEl) tableEl.hidden = true;
      if (emptyMsg) emptyMsg.hidden = false;
      return;
    }

    if (tableEl) tableEl.hidden = false;
    if (emptyMsg) emptyMsg.hidden = true;

    tbody.innerHTML = visible.map(j => `
      <tr id="qrow-${j.pk}" class="${j._selected ? 'is-selected' : ''}">
        <td class="queue-select-col">
          <input type="checkbox" class="queue-job-cb" value="${j.pk}" ${j._selected ? 'checked' : ''}>
        </td>
        <td><a href="${j.detail_url}" style="color:inherit;font-size:.8125rem">${sourceLabel(j.source)}</a></td>
        <td style="color:#9ca3af;font-size:.8125rem;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${j.from_address || ''}">${j.from_address || '—'}</td>
        <td style="color:#9ca3af;font-size:.8125rem;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${j.notes || ''}">${j.notes || ''}</td>
        <td>${statusBadge(j.status)}</td>
        <td style="color:#9ca3af;font-size:.8125rem">${j.duration != null ? j.duration + 's' : '—'}</td>
        <td style="color:#9ca3af;font-size:.8125rem;white-space:nowrap">${j.started}</td>
      </tr>
    `).join('');

    // Checkbox listeners
    tbody.querySelectorAll('.queue-job-cb').forEach(cb => {
      cb.addEventListener('change', () => {
        const job = allJobs.find(j => String(j.pk) === cb.value);
        if (job) job._selected = cb.checked;
        updateBulkBar();
      });
    });
  }

  // ── Fetch jobs ────────────────────────────────────────────────────────────
  function fetchJobs() {
    fetch('/dashboard/queue/status/', { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return;
        // Preserve selection state
        const selected = new Set(allJobs.filter(j => j._selected).map(j => j.pk));
        allJobs = data.jobs.map(j => ({
          ...j,
          pk: j.id,
          duration: j.duration_seconds,
          started: j.created_at ? j.created_at.slice(0, 16).replace('T', ' ') : '—',
          detail_url: `/dashboard/queue/${j.id}/`,
          _selected: selected.has(j.id),
        }));
        render();

        const hasActive = allJobs.some(j => j.status === 'queued' || j.status === 'processing');
        pollTimer = setTimeout(fetchJobs, hasActive ? 4000 : 12000);
      })
      .catch(() => { pollTimer = setTimeout(fetchJobs, 20000); });
  }

  if (tbody) {
    fetchJobs();
    document.addEventListener('visibilitychange', () => {
      clearTimeout(pollTimer);
      if (!document.hidden) fetchJobs();
    });
  }

  if (searchInput) searchInput.addEventListener('input', render);
  if (filterSel)   filterSel.addEventListener('change', render);

  // ── Select mode ───────────────────────────────────────────────────────────
  const enterSelect  = document.getElementById('queue-enter-select');
  const bulkBar      = document.getElementById('queue-bulk-bar');
  const bulkCountEl  = document.getElementById('queue-selected-count');
  const cancelSelect = document.getElementById('queue-cancel-select');
  const bulkDelete   = document.getElementById('queue-bulk-delete');
  const selectAll    = document.getElementById('queue-select-all');

  if (enterSelect) enterSelect.hidden = false;

  function updateBulkBar() {
    const n = allJobs.filter(j => j._selected).length;
    if (bulkCountEl) bulkCountEl.textContent = n + ' selected';
    if (bulkBar) bulkBar.classList.toggle('is-active', n > 0);
  }

  if (enterSelect) {
    enterSelect.addEventListener('click', () => {
      bulkBar && bulkBar.classList.add('is-active');
    });
  }
  if (cancelSelect) {
    cancelSelect.addEventListener('click', () => {
      allJobs.forEach(j => { j._selected = false; });
      if (selectAll) selectAll.checked = false;
      render();
      updateBulkBar();
    });
  }
  if (selectAll) {
    selectAll.addEventListener('change', () => {
      allJobs.forEach(j => { j._selected = selectAll.checked; });
      render();
      updateBulkBar();
    });
  }

  if (bulkDelete) {
    bulkDelete.addEventListener('click', () => {
      const pks = allJobs.filter(j => j._selected).map(j => j.pk);
      if (!pks.length) return;
      if (!confirm(`Delete ${pks.length} job(s)?`)) return;

      fetch('/dashboard/queue/bulk-delete/', {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrf(),
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({ pks }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.ok) {
            allJobs = allJobs.filter(j => !pks.includes(j.pk));
            render();
            updateBulkBar();
          }
        })
        .catch(() => alert('Error deleting jobs.'));
    });
  }

})();