// project/static/manual/js/pages/rule_add.js
(function () {
  function getCsrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content && meta.content !== 'NOTPROVIDED') return meta.content;
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }

  // ─── Rule type registry ────────────────────────────────────────────────────
  var RULE_TYPES = {
    sender: {
      collectData: function (form) {
        var catSelect = form.querySelector('[name="category_id"]');
        return {
          rule_type: 'sender',
          pattern: form.querySelector('[name="pattern"]').value.trim(),
          action: form.querySelector('[name="action"]').value,
          category_id: catSelect ? catSelect.value || null : null,
        };
      },
    },
    keyword: {
      collectData: function (form) {
        var catSelect = form.querySelector('[name="category_id"]');
        return {
          rule_type: 'keyword',
          pattern: form.querySelector('[name="pattern"]').value.trim(),
          action: form.querySelector('[name="action"]').value,
          category_id: catSelect ? catSelect.value || null : null,
        };
      },
    },
    prompt: {
      collectData: function (form) {
        return {
          rule_type: 'prompt',
          prompt_text: form.querySelector('[name="prompt_text"]').value.trim(),
          pattern: (form.querySelector('[name="prompt_pattern"]') || { value: '' }).value.trim(),
        };
      },
    },
  };

  // ─── Rule type dropdown → show/hide field blocks ──────────────────────────
  var typeSelect = document.getElementById('rule-type-select');

  function showFieldsForType(type) {
    document.querySelectorAll('.rule-fields').forEach(function (el) {
      el.hidden = true;
    });
    if (type && document.getElementById('rule-fields-' + type)) {
      document.getElementById('rule-fields-' + type).hidden = false;
    }
  }

  if (typeSelect) {
    typeSelect.addEventListener('change', function () {
      showFieldsForType(typeSelect.value);
    });
    showFieldsForType(typeSelect.value);
  }

  // ─── Category select visibility ──────────────────────────────────────────
  function toggleCatGroup(sel) {
    var catGroup = sel.closest('form').querySelector('.cat-select-group');
    if (catGroup) catGroup.hidden = sel.value !== 'categorize';
  }

  document.querySelectorAll('select[name="action"]').forEach(function (sel) {
    sel.addEventListener('change', function () { toggleCatGroup(sel); });
    toggleCatGroup(sel);
  });

  // ─── Submit ───────────────────────────────────────────────────────────────
  document.querySelectorAll('.rule-add-form').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var ruleType = form.dataset.ruleType;
      var handler = RULE_TYPES[ruleType];
      if (!handler) { alert('Unknown rule type: ' + ruleType); return; }

      var data = handler.collectData(form);
      var btn = form.querySelector('button[type="submit"]');
      btn.disabled = true;

      var addUrl = form.dataset.addUrl;
      fetch(addUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
        body: JSON.stringify(data),
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          if (res.ok) {
            window.location.href = form.dataset.cancelUrl;
          } else {
            alert('Error: ' + (res.error || 'Unknown error'));
            btn.disabled = false;
          }
        })
        .catch(function (err) {
          alert('Network error: ' + err.message);
          btn.disabled = false;
        });
    });
  });
})();
