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
    if (input.files.length) showFiles(input.files);
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
    if (e.dataTransfer.files.length) {
      input.files = e.dataTransfer.files;
      showFiles(input.files);
    }
  });

  if (form) {
    form.addEventListener('submit', function (e) {
      var contextEl = document.getElementById('context');
      var hasFile = input.files.length > 0;
      var hasPrompt = contextEl && contextEl.value.trim().length > 0;
      if (!hasFile && !hasPrompt) {
        e.preventDefault();
        if (errorEl) {
          errorEl.textContent = 'Please provide a file or a prompt.';
          errorEl.hidden = false;
        }
      }
    });
  }

  function showFiles(files) {
    if (fileName) {
      var names = [];
      for (var i = 0; i < files.length; i++) names.push(files[i].name);
      fileName.textContent = names.join(', ');
    }
    if (preview)   preview.hidden = false;
    if (errorEl)   errorEl.hidden = true;
  }

  window.clearFile = function () {
    input.value = '';
    if (preview)   preview.hidden = true;
  };
}());
