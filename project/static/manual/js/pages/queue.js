// project/static/manual/js/pages/queue.js
(function () {
  var tbody = document.getElementById('queue-tbody');
  var table = document.getElementById('queue-table');
  var emptyMsg = document.getElementById('queue-empty-msg');
  if (!tbody) return;

  var QUEUE_STATUS_URL = document.body.dataset.queueStatusUrl;
  var pollInterval = null;
  var POLL_MS = 4000;

  var SOURCE_LABELS = { email: 'Email', upload: 'Upload' };
  var STATUS_LABELS = {
    queued: 'Queued',
    processing: 'Processing…',
    needs_review: 'Needs review',
    done: 'Done',
    failed: 'Failed',
  };
  var STATUS_CLASSES = {
    queued: 'status--queued',
    processing: 'status--processing',
    needs_review: 'status--needs-review',
    done: 'status--done',
    failed: 'status--failed',
  };

  var FAILURE_REASON_LABELS = {
    llm_error: 'AI service error',
    scan_limit: 'Scan limit reached',
    pro_required: 'Pro plan required',
    internal_error: 'Internal error',
    discarded_by_rule: 'Discarded by rule',
  };

  // ─── Filter state ─────────────────────────────────────────────────────────

  var filterStatus = '';
  var filterSource = '';
  var filterSearch = '';

  var filterStatusEl = document.getElementById('queue-filter-status');
  var filterSourceEl = document.getElementById('queue-filter-source');
  var filterSearchEl = document.getElementById('queue-search');

  if (filterStatusEl) {
    filterStatusEl.addEventListener('change', function () {
      filterStatus = filterStatusEl.value;
      currentPage = 1;
      if (lastJobs) render(lastJobs);
    });
  }
  if (filterSourceEl) {
    filterSourceEl.addEventListener('change', function () {
      filterSource = filterSourceEl.value;
      currentPage = 1;
      if (lastJobs) render(lastJobs);
    });
  }
  if (filterSearchEl) {
    filterSearchEl.addEventListener('input', function () {
      filterSearch = filterSearchEl.value.toLowerCase().trim();
      currentPage = 1;
      if (lastJobs) render(lastJobs);
    });
  }

  var lastJobs = null;
  var currentPage = 1;
  var PAGE_SIZE = 25;

  // ─── Select mode ──────────────────────────────────────────────────────────

  var selectMode = false;
  var selectedIds = new Set();
  var bulkBar = document.getElementById('queue-bulk-bar');
  var selectedCountEl = document.getElementById('queue-selected-count');
  var bulkDeleteBtn = document.getElementById('queue-bulk-delete');
  var cancelSelectBtn = document.getElementById('queue-cancel-select');
  var enterSelectBtn = document.getElementById('queue-enter-select');
  var selectAllCb = document.getElementById('queue-select-all');
  var selectColHeaders = document.querySelectorAll('.queue-select-col');

  function updateSelectedCount() {
    if (selectedCountEl) selectedCountEl.textContent = selectedIds.size + ' selected';
    if (bulkDeleteBtn) bulkDeleteBtn.disabled = selectedIds.size === 0;
  }

  function enterSelectMode() {
    selectMode = true;
    selectedIds.clear();
    if (bulkBar) bulkBar.hidden = false;
    if (enterSelectBtn) enterSelectBtn.hidden = true;
    selectColHeaders.forEach(function (h) { h.hidden = false; });
    if (lastJobs) render(lastJobs);
    updateSelectedCount();
  }

  function exitSelectMode() {
    selectMode = false;
    selectedIds.clear();
    if (bulkBar) bulkBar.hidden = true;
    if (enterSelectBtn) enterSelectBtn.hidden = false;
    selectColHeaders.forEach(function (h) { h.hidden = true; });
    if (selectAllCb) selectAllCb.checked = false;
    if (lastJobs) render(lastJobs);
  }

  if (enterSelectBtn) enterSelectBtn.addEventListener('click', enterSelectMode);
  if (cancelSelectBtn) cancelSelectBtn.addEventListener('click', exitSelectMode);

  if (selectAllCb) {
    selectAllCb.addEventListener('change', function () {
      var cbs = tbody.querySelectorAll('.queue-row-cb');
      cbs.forEach(function (cb) {
        cb.checked = selectAllCb.checked;
        var id = parseInt(cb.dataset.jobId, 10);
        if (selectAllCb.checked) selectedIds.add(id); else selectedIds.delete(id);
      });
      updateSelectedCount();
    });
  }

  if (bulkDeleteBtn) {
    bulkDeleteBtn.addEventListener('click', function () {
      if (selectedIds.size === 0) return;
      if (!confirm('Delete ' + selectedIds.size + ' job' + (selectedIds.size !== 1 ? 's' : '') + '? Stored data will be permanently removed. Events already created are kept.')) return;
      var CSRF = document.querySelector('meta[name="csrf-token"]').content;
      bulkDeleteBtn.disabled = true;
      bulkDeleteBtn.textContent = 'Deleting…';
      fetch('/dashboard/queue/bulk-delete/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
        body: JSON.stringify({ ids: Array.from(selectedIds) }),
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            exitSelectMode();
            poll();
          } else {
            alert(data.error || 'Delete failed.');
            bulkDeleteBtn.disabled = false;
            bulkDeleteBtn.textContent = 'Delete selected';
          }
        })
        .catch(function () {
          alert('Network error.');
          bulkDeleteBtn.disabled = false;
          bulkDeleteBtn.textContent = 'Delete selected';
        });
    });
  }

  function applyFilters(jobs) {
    return jobs.filter(function (j) {
      if (filterStatus && j.status !== filterStatus) return false;
      if (filterSource && j.source !== filterSource) return false;
      if (filterSearch) {
        var haystack = ((j.from_address || '') + ' ' + (j.notes || '') + ' ' + (j.source || '')).toLowerCase();
        if (haystack.indexOf(filterSearch) === -1) return false;
      }
      return true;
    });
  }

  // ─── Helpers ─────────────────────────────────────────────────────────────

  function fmt(isoStr) {
    var d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) +
           ' ' + d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function fmtDuration(secs) {
    if (secs < 2) return '< 1s';
    if (secs < 60) return secs + 's';
    return Math.floor(secs / 60) + 'm ' + (secs % 60) + 's';
  }

  // ─── Render ───────────────────────────────────────────────────────────────

  function render(jobs) {
    var visible = applyFilters(jobs);

    // Show/hide select button based on data availability
    if (enterSelectBtn) enterSelectBtn.hidden = !visible || visible.length === 0;

    if (!visible || visible.length === 0) {
      table.hidden = true;
      emptyMsg.hidden = false;
      emptyMsg.textContent = jobs.length > 0
        ? 'No jobs match the current filters.'
        : 'No jobs yet.';
      removePagination();
      return;
    }
    emptyMsg.hidden = true;
    table.hidden = false;

    // Pagination
    var totalPages = Math.ceil(visible.length / PAGE_SIZE);
    if (currentPage > totalPages) currentPage = totalPages;
    var start = (currentPage - 1) * PAGE_SIZE;
    var pageItems = visible.slice(start, start + PAGE_SIZE);

    tbody.innerHTML = '';

    pageItems.forEach(function (j) {
      var tr = document.createElement('tr');

      var isTerminal = j.status === 'done' || j.status === 'failed' || j.status === 'needs_review';
      var hasPending = j.pending_event_count > 0;
      var isFailed = j.status === 'failed';
      var isDiscarded = j.status === 'done' && j.notes && j.notes.startsWith('Discarded —');

      var attentionBadge = hasPending
        ? '<span class="queue-pending-badge">' + j.pending_event_count + ' pending</span>'
        : '';

      var activeInfo = j.active_event_count > 0
        ? j.active_event_count + ' event' + (j.active_event_count !== 1 ? 's' : '') + ' created'
        : (j.status === 'done' && !hasPending && !isDiscarded ? 'No events' : '');

      // For failed jobs, surface the reason in the notes cell.
      // For discarded-by-rule jobs, the note IS the message — surface it directly.
      var notesCell;
      if (isFailed && j.failure_reason) {
        var failureLabel = FAILURE_REASON_LABELS[j.failure_reason] || j.failure_reason;
        notesCell = '<span style="color:#dc2626;font-weight:500;">' + failureLabel + '</span>'
          + (j.notes ? ' · ' + j.notes : '');
      } else if (isDiscarded) {
        notesCell = '<span class="queue-discarded-note">' + j.notes + '</span>';
      } else {
        notesCell = (j.notes || '') + (j.notes && activeInfo ? ' · ' : '') + (activeInfo || '');
      }

      var sourceLabel = SOURCE_LABELS[j.source] || j.source;
      var sourceCell = isTerminal
        ? '<a href="/dashboard/queue/' + j.id + '/" class="queue-source-link">' + sourceLabel + '</a>'
        : sourceLabel;

      var isDurationVisible = j.status === 'done' || j.status === 'failed' || j.status === 'needs_review';

      var cbCell = selectMode
        ? '<td><input type="checkbox" class="queue-row-cb" data-job-id="' + j.id + '"' + (selectedIds.has(j.id) ? ' checked' : '') + '></td>'
        : '';

      tr.innerHTML = cbCell +
        '<td>' + sourceCell + attentionBadge + '</td>' +
        '<td class="queue-from" data-label="From">' + (j.from_address || '—') + '</td>' +
        '<td class="queue-notes" data-label="Notes">' + notesCell + '</td>' +
        '<td data-label="Status"><span class="queue-status ' + (STATUS_CLASSES[j.status] || '') + '">' + (STATUS_LABELS[j.status] || j.status) + '</span></td>' +
        '<td data-label="Duration">' + (isDurationVisible ? fmtDuration(j.duration_seconds) : '—') + '</td>' +
        '<td data-label="Started">' + fmt(j.created_at) + '</td>';

      if (isTerminal && !selectMode) {
        tr.classList.add('queue-row--clickable');
        tr.addEventListener('click', function (e) {
          if (e.target.tagName !== 'A') {
            window.location.href = '/dashboard/queue/' + j.id + '/';
          }
        });
      }

      // In select mode, wire up checkbox
      if (selectMode) {
        var cb = tr.querySelector('.queue-row-cb');
        if (cb) {
          cb.addEventListener('change', function () {
            var id = parseInt(cb.dataset.jobId, 10);
            if (cb.checked) selectedIds.add(id); else selectedIds.delete(id);
            updateSelectedCount();
          });
        }
      }

      tbody.appendChild(tr);
    });

    renderPagination(totalPages);
  }

  // ─── Client-side pagination nav ──────────────────────────────────────────

  function removePagination() {
    var existing = document.getElementById('queue-pagination');
    if (existing) existing.remove();
  }

  function renderPagination(totalPages) {
    removePagination();
    if (totalPages <= 1) return;

    var nav = document.createElement('nav');
    nav.id = 'queue-pagination';
    nav.className = 'pagination';

    if (currentPage > 1) {
      var prev = document.createElement('a');
      prev.href = '#'; prev.className = 'pagination__link'; prev.textContent = '←';
      prev.addEventListener('click', function (e) { e.preventDefault(); currentPage--; render(lastJobs); });
      nav.appendChild(prev);
    }

    for (var i = 1; i <= totalPages; i++) {
      if (i === currentPage) {
        var active = document.createElement('span');
        active.className = 'pagination__num pagination__num--active';
        active.textContent = i;
        nav.appendChild(active);
      } else if (i === 1 || i === totalPages || (i >= currentPage - 2 && i <= currentPage + 2)) {
        var link = document.createElement('a');
        link.href = '#'; link.className = 'pagination__num'; link.textContent = i;
        (function (p) {
          link.addEventListener('click', function (e) { e.preventDefault(); currentPage = p; render(lastJobs); });
        })(i);
        nav.appendChild(link);
      } else if (i === currentPage - 3 || i === currentPage + 3) {
        var ellipsis = document.createElement('span');
        ellipsis.className = 'pagination__ellipsis'; ellipsis.textContent = '…';
        nav.appendChild(ellipsis);
      }
    }

    if (currentPage < totalPages) {
      var next = document.createElement('a');
      next.href = '#'; next.className = 'pagination__link'; next.textContent = '→';
      next.addEventListener('click', function (e) { e.preventDefault(); currentPage++; render(lastJobs); });
      nav.appendChild(next);
    }

    table.parentNode.insertBefore(nav, table.nextSibling);
  }

  function hasActive(jobs) {
    return jobs && jobs.some(function (j) { return j.status === 'queued' || j.status === 'processing'; });
  }

  // ─── Poll ─────────────────────────────────────────────────────────────────

  function poll() {
    fetch(QUEUE_STATUS_URL, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        lastJobs = data.jobs;
        render(lastJobs);
        if (!hasActive(data.jobs) && pollInterval) {
          clearInterval(pollInterval);
          pollInterval = null;
        }
      })
      .catch(function () {});
  }

  poll();
  pollInterval = setInterval(poll, POLL_MS);
})();