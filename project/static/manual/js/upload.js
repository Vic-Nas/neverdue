/* static/manual/js/upload.js */

(function () {
  var input     = document.getElementById('file-input');
  var dropzone  = document.getElementById('dropzone');
  var preview   = document.getElementById('file-preview');
  var fileName  = document.getElementById('file-name');
  var form      = document.getElementById('upload-form');
  var errorEl   = document.getElementById('upload-error');

  if (!input || !dropzone) return;

  input.addEventListener('change', function () {
    if (input.files.length) showFile(input.files[0]);
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
    var file = e.dataTransfer.files[0];
    if (file) {
      input.files = e.dataTransfer.files;
      showFile(file);
    }
  });

  if (form) {
    form.addEventListener('submit', function (e) {
      if (!input.files.length) {
        e.preventDefault();
        if (errorEl) {
          errorEl.textContent = 'Please select a file first using the "Browse files" button above.';
          errorEl.hidden = false;
        }
      }
    });
  }

  function showFile(file) {
    if (fileName)  fileName.textContent = file.name;
    if (preview)   preview.hidden = false;
    if (errorEl)   errorEl.hidden = true;
  }

  window.clearFile = function () {
    input.value = '';
    if (preview)   preview.hidden = true;
  };
}());
