/* static/manual/js/upload.js */

(function () {
  var input     = document.getElementById('file-input');
  var dropzone  = document.getElementById('dropzone');
  var preview   = document.getElementById('file-preview');
  var fileName  = document.getElementById('file-name');
  var form      = document.getElementById('upload-form');

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
        dropzone.classList.add('upload-dropzone--error');
        setTimeout(function () { dropzone.classList.remove('upload-dropzone--error'); }, 1500);
      }
    });
  }

  function showFile(file) {
    if (fileName)  fileName.textContent = file.name;
    if (preview)   preview.hidden = false;
  }

  window.clearFile = function () {
    input.value = '';
    if (preview)   preview.hidden = true;
  };
}());
