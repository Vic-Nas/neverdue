/* static/manual/js/upload.js */

(function () {
  const input     = document.getElementById('file-input');
  const dropzone  = document.getElementById('dropzone');
  const preview   = document.getElementById('file-preview');
  const fileName  = document.getElementById('file-name');
  const submitBtn = document.getElementById('submit-btn');

  if (!input || !dropzone) return;

  input.addEventListener('change', () => {
    if (input.files.length) showFile(input.files[0]);
  });

  dropzone.addEventListener('dragover', e => {
    e.preventDefault();
    dropzone.classList.add('upload-dropzone--active');
  });

  dropzone.addEventListener('dragleave', () => {
    dropzone.classList.remove('upload-dropzone--active');
  });

  dropzone.addEventListener('drop', e => {
    e.preventDefault();
    dropzone.classList.remove('upload-dropzone--active');
    const file = e.dataTransfer.files[0];
    if (file) {
      input.files = e.dataTransfer.files;
      showFile(file);
    }
  });

  function showFile(file) {
    if (fileName)  fileName.textContent = file.name;
    if (preview)   preview.hidden = false;
    if (submitBtn) submitBtn.disabled = false;
  }

  window.clearFile = function () {
    input.value = '';
    if (preview)   preview.hidden = true;
    if (submitBtn) submitBtn.disabled = true;
  };
}());
