/* static/manual/js/upload.js */

(function () {
  var input    = document.getElementById('file-input');
  var dropzone = document.getElementById('dropzone');
  var listEl   = document.getElementById('file-list');
  var form     = document.getElementById('upload-form');
  var errorEl  = document.getElementById('upload-error');
  var files    = [];

  if (!input || !dropzone) return;

  input.addEventListener('change', function () {
    addFiles(input.files);
    input.value = '';
  });

  dropzone.addEventListener('dragover', function (e) {
    e.preventDefault();
    dropzone.classList.add('upload-dropzone--active');
  });

  dropzone.addEventListener('dragleave', function () {
    dropzone.classList.remove('upload-dropzone--active');
  });

  dropzone.addEventListener('drop', function (e) {
    e.preventDefault();
    dropzone.classList.remove('upload-dropzone--active');
    addFiles(e.dataTransfer.files);
  });

  if (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var contextEl = document.getElementById('context');
      var hasPrompt = contextEl && contextEl.value.trim().length > 0;
      if (!files.length && !hasPrompt) {
        if (errorEl) {
          errorEl.textContent = 'Please provide a file or a prompt.';
          errorEl.hidden = false;
        }
        return;
      }
      var fd = new FormData(form);
      fd.delete('files');
      for (var i = 0; i < files.length; i++) fd.append('files', files[i]);
      var btn = document.getElementById('submit-btn');
      if (btn) btn.disabled = true;
      fetch(form.action || '', { method: 'POST', body: fd })
        .then(function (r) { window.location.href = r.url; })
        .catch(function () { if (btn) btn.disabled = false; });
    });
  }

  function addFiles(fileList) {
    for (var i = 0; i < fileList.length; i++) files.push(fileList[i]);
    renderList();
    if (errorEl) errorEl.hidden = true;
  }

  function removeFile(idx) {
    files.splice(idx, 1);
    renderList();
  }

  function renderList() {
    if (!listEl) return;
    listEl.innerHTML = '';
    for (var i = 0; i < files.length; i++) {
      var li = document.createElement('li');
      li.className = 'upload-file-list__item';
      var span = document.createElement('span');
      span.className = 'upload-file-list__name';
      span.textContent = files[i].name;
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'upload-file-list__remove';
      btn.textContent = '✕';
      btn.setAttribute('data-idx', i);
      btn.addEventListener('click', function () { removeFile(Number(this.getAttribute('data-idx'))); });
      li.appendChild(span);
      li.appendChild(btn);
      listEl.appendChild(li);
    }
  }
}());
