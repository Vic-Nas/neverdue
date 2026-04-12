/* js/pages/upload.js — import / file upload page */

(function () {
  'use strict';

  const dropzone  = document.getElementById('dropzone');
  const fileInput = document.getElementById('file-input');
  const fileList  = document.getElementById('file-list');
  const submitBtn = document.getElementById('submit-btn');
  const errorEl   = document.getElementById('upload-error');
  const form      = document.getElementById('upload-form');

  if (!dropzone || !fileInput) return;

  // DataTransfer to accumulate files across multiple picks
  let dt = new DataTransfer();

  function renderFileList() {
    if (!fileList) return;
    fileList.innerHTML = '';
    [...dt.files].forEach((file, i) => {
      const li = document.createElement('li');
      li.innerHTML = `
        <span>${file.name} <span style="color:#6b7280">(${(file.size/1024).toFixed(0)} KB)</span></span>
        <button type="button" class="remove-file-btn" data-index="${i}">✕</button>
      `;
      fileList.appendChild(li);
    });
    // Sync DataTransfer into the actual input
    fileInput.files = dt.files;
  }

  // ── File list remove ──────────────────────────────────────────────────────
  if (fileList) {
    fileList.addEventListener('click', (e) => {
      const btn = e.target.closest('.remove-file-btn');
      if (!btn) return;
      const idx = parseInt(btn.dataset.index);
      const newDt = new DataTransfer();
      [...dt.files].forEach((f, i) => { if (i !== idx) newDt.items.add(f); });
      dt = newDt;
      renderFileList();
    });
  }

  // ── File input change ─────────────────────────────────────────────────────
  fileInput.addEventListener('change', () => {
    [...fileInput.files].forEach(f => dt.items.add(f));
    renderFileList();
    fileInput.value = '';
  });

  // ── Drag and drop ─────────────────────────────────────────────────────────
  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('drag-over');
  });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('drag-over');
    const files = e.dataTransfer ? [...e.dataTransfer.files] : [];
    files.forEach(f => dt.items.add(f));
    renderFileList();
  });

  // ── Submit guard ──────────────────────────────────────────────────────────
  if (form && submitBtn) {
    form.addEventListener('submit', (e) => {
      const ctx = form.querySelector('[name="context"]')?.value.trim();
      if (!dt.files.length && !ctx) {
        e.preventDefault();
        if (errorEl) {
          errorEl.textContent = 'Please upload at least one file or add context.';
          errorEl.hidden = false;
        }
        return;
      }
      submitBtn.disabled = true;
      submitBtn.textContent = 'Submitting…';
    });
  }

})();
