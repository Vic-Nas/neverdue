(function () {
  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  var pageEl = document.querySelector('.page[data-rule-add-url]');
  var ADD_URL = pageEl ? pageEl.dataset.ruleAddUrl : '';
  var DELETE_URL_TPL = pageEl ? pageEl.dataset.ruleDeleteUrlTpl : '';

  // ─── Rule type registry ────────────────────────────────────────────────────
  //
  // To add a new rule type, add an entry here. Keys map to the option values in
  // the <select> and to the id of the matching fields block (rule-fields-{type}).
  //
  // collectData(form) must return the payload object sent to the server.
  // It receives the <form> element inside the matching rule-fields block.
  //
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
    // ── Example: add a new "domain" rule type ────────────────────────────────
    // domain: {
    //   collectData: function (form) {
    //     var catSelect = form.querySelector('[name="category_id"]');
    //     return {
    //       rule_type: 'domain',
    //       pattern: form.querySelector('[name="pattern"]').value.trim(),
    //       action: form.querySelector('[name="action"]').value,
    //       category_id: catSelect ? catSelect.value || null : null,
    //     };
    //   },
    // },
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
    // Restore selection on back-navigation
    showFieldsForType(typeSelect.value);
  }

  // ─── Category select visibility (action → categorize / discard) ───────────

  document.querySelectorAll('select[name="action"]').forEach(function (sel) {
    function toggle() {
      var catGroup = sel.closest('form').querySelector('.cat-select-group');
      if (catGroup) catGroup.hidden = sel.value !== 'categorize';
    }
    sel.addEventListener('change', toggle);
    toggle();
  });

  // ─── Submit ───────────────────────────────────────────────────────────────

  function addRule(form, ruleType) {
    var handler = RULE_TYPES[ruleType];
    if (!handler) {
      alert('Unknown rule type: ' + ruleType);
      return;
    }

    var data = handler.collectData(form);
    var btn = form.querySelector('button[type="submit"]');
    btn.disabled = true;

    fetch(ADD_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify(data),
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.ok) {
          location.reload();
        } else {
          alert('Error: ' + res.error);
          btn.disabled = false;
        }
      })
      .catch(function () {
        alert('Network error.');
        btn.disabled = false;
      });
  }

  document.querySelectorAll('.rule-add-form').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      addRule(form, form.dataset.ruleType);
    });
  });

  // ─── Delete ───────────────────────────────────────────────────────────────

  function deleteRule(pk, row) {
    fetch(DELETE_URL_TPL.replace('__PK__', pk), {
      method: 'POST',
      headers: { 'X-CSRFToken': CSRF },
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) row.remove();
        else alert('Error: ' + data.error);
      })
      .catch(function () { alert('Network error.'); });
  }

  document.querySelectorAll('.rule-delete-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var row = btn.closest('.rule-row');
      deleteRule(btn.dataset.pk, row);
    });
  });
})();