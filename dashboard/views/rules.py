# dashboard/views/rules.py
import json as _json
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render

from dashboard.models import Category, Rule

logger = logging.getLogger(__name__)


@login_required
def rules(request):
    try:
        rules_qs = Rule.objects.filter(user=request.user).select_related('category').order_by('rule_type', 'created_at')
        categories = Category.objects.filter(user=request.user).order_by('name')
        return render(request, 'dashboard/rules.html', {'rules': rules_qs, 'categories': categories})
    except Exception:
        logger.exception("rules error for user=%s", request.user.pk)
        return HttpResponse('Rules unavailable.', status=500)


@login_required
def rule_add(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        data = _json.loads(request.body)
        rule_type = data.get('rule_type', '').strip()
        pattern = data.get('pattern', '').strip()
        action = data.get('action', '').strip()
        category_id = data.get('category_id')
        prompt_text = data.get('prompt_text', '').strip()

        if not rule_type:
            return JsonResponse({'ok': False, 'error': 'Rule type is required.'}, status=400)

        if rule_type == Rule.TYPE_PROMPT:
            if not prompt_text:
                return JsonResponse({'ok': False, 'error': 'Prompt text is required.'}, status=400)
            rule = Rule.objects.create(user=request.user, rule_type=rule_type, pattern=pattern, prompt_text=prompt_text)
            logger.info("rule_add: created prompt rule | user=%s rule_id=%s", request.user.pk, rule.pk)
            return JsonResponse({'ok': True})

        if not action:
            return JsonResponse({'ok': False, 'error': 'Action is required.'}, status=400)

        if action in (Rule.ACTION_ALLOW, Rule.ACTION_BLOCK) and rule_type != Rule.TYPE_SENDER:
            return JsonResponse({'ok': False, 'error': 'Allow and block actions are only valid for sender rules.'}, status=400)

        if action == Rule.ACTION_CATEGORIZE and not category_id:
            return JsonResponse({'ok': False, 'error': 'A category is required for categorize action.'}, status=400)

        category = None
        if category_id:
            category = get_object_or_404(Category, pk=category_id, user=request.user)

        Rule.objects.create(user=request.user, rule_type=rule_type, pattern=pattern, action=action, category=category)
        logger.info("rule_add: created %s rule | user=%s pattern=%s action=%s", rule_type, request.user.pk, pattern, action)
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("rule_add error for user=%s", request.user.pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)


@login_required
def rule_delete(request, pk):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Method not allowed'}, status=405)
    try:
        rule = get_object_or_404(Rule, pk=pk, user=request.user)
        rule.delete()
        return JsonResponse({'ok': True})
    except Exception:
        logger.exception("rule_delete error for user=%s pk=%s", request.user.pk, pk)
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)
