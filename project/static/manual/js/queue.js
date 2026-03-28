(function () {
  var tbody = document.getElementById('queue-tbody');
  var table = document.getElementById('queue-table');
  var emptyMsg = document.getElementById('queue-empty-msg');
  if (!tbody) return;

  var QUEUE_STATUS_URL = document.body.dataset.queueStatusUrl;
  var pollInterval = null;
  var POLL_MS = 4000;

  var SOURCE_LABELS = { email: 'Email', upload: 'Upload' };
  var STATUS_LABELS = { queued: 'Queued', processing: 'Processing…', needs_review: 'Needs review', done: 'Done', failed: 'Failed' };
  var STATUS_CLASSES = { queued: 'status--queued', processing: 'status--processing', needs_review: 'status--needs-review', done: 'status--done', failed: 'status--failed' };

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

  function render(jobs) {
    if (!jobs || jobs.length === 0) {
      table.hidden = true;
      emptyMsg.hidden = false;
      return;
    }
    emptyMsg.hidden = true;
    table.hidden = false;
    tbody.innerHTML = '';

    jobs.forEach(function (j) {
      var tr = document.createElement('tr');

      var isTerminal = j.status === 'done' || j.status === 'failed' || j.status === 'needs_review';
      var hasPending = j.pending_event_count > 0;

      var attentionBadge = hasPending
        ? '<span class="queue-pending-badge">' + j.pending_event_count + ' pending</span>'
        : '';

      var activeInfo = j.active_event_count > 0
        ? j.active_event_count + ' event' + (j.active_event_count !== 1 ? 's' : '') + ' created'
        : (j.status === 'done' && !hasPending ? 'No events' : '');

      var notesCell = (j.notes || '') + (j.notes && activeInfo ? ' · ' : '') + (activeInfo || '');

      var sourceLabel = SOURCE_LABELS[j.source] || j.source;
      var sourceCell = isTerminal
        ? '<a href="/dashboard/queue/' + j.id + '/" class="queue-source-link">' + sourceLabel + '</a>'
        : sourceLabel;

      var isDurationVisible = j.status === 'done' || j.status === 'failed' || j.status === 'needs_review';

      tr.innerHTML =
        '<td>' + sourceCell + attentionBadge + '</td>' +
        '<td class="queue-from" data-label="From">' + (j.from_address || '—') + '</td>' +
        '<td class="queue-notes" data-label="Notes">' + notesCell + '</td>' +
        '<td data-label="Status"><span class="queue-status ' + (STATUS_CLASSES[j.status] || '') + '">' + (STATUS_LABELS[j.status] || j.status) + '</span></td>' +
        '<td data-label="Duration">' + (isDurationVisible ? fmtDuration(j.duration_seconds) : '—') + '</td>' +
        '<td data-label="Started">' + fmt(j.created_at) + '</td>';

      if (isTerminal) {
        tr.classList.add('queue-row--clickable');
        tr.addEventListener('click', function (e) {
          if (e.target.tagName !== 'A') {
            window.location.href = '/dashboard/queue/' + j.id + '/';
          }
        });
      }

      tbody.appendChild(tr);
    });
  }

  function hasActive(jobs) {
    return jobs && jobs.some(function (j) { return j.status === 'queued' || j.status === 'processing'; });
  }

  function poll() {
    fetch(QUEUE_STATUS_URL, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        render(data.jobs);
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
