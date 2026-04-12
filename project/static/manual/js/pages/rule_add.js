/* js/pages/rule_add.js — rule add/edit form dynamic fields */

(function () {
  'use strict';

  // Rule type radios drive which fields are visible
  const typeRadios   = document.querySelectorAll('input[name="rule_type"]');
  const actionGroup  = document.getElementById('action-group');
  const patternGroup = document.getElementById('pattern-group');
  const promptGroup  = document.getElementById('prompt-group');
  const patternLabel = document.getElementById('pattern-label');

  function update() {
    const type = document.querySelector('input[name="rule_type"]:checked')?.value;
    if (!type) return;

    const isPrompt  = type === 'prompt';
    const isSender  = type === 'sender';
    const isKeyword = type === 'keyword';

    if (actionGroup)  actionGroup.style.display  = isPrompt ? 'none' : '';
    if (promptGroup)  promptGroup.style.display   = isPrompt ? '' : 'none';
    if (patternGroup) {
      // For prompt rules, pattern is optional (sender filter) — always show it
      patternGroup.style.display = '';
      if (patternLabel) {
        patternLabel.textContent = isPrompt  ? 'Sender filter (optional)'
                                 : isSender  ? 'Sender / domain'
                                 : 'Keyword';
      }
    }
  }

  typeRadios.forEach(r => r.addEventListener('change', update));
  update();

  // Action radio drives category picker
  const actionRadios = document.querySelectorAll('input[name="action"]');
  const categoryGroup = document.getElementById('category-group');

  function updateAction() {
    const action = document.querySelector('input[name="action"]:checked')?.value;
    if (categoryGroup) categoryGroup.style.display = (action === 'categorize') ? '' : 'none';
  }

  actionRadios.forEach(r => r.addEventListener('change', updateAction));
  updateAction();

})();
