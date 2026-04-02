# accounts/views/timezone.py
import json
import zoneinfo

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

VALID_TIMEZONES = zoneinfo.available_timezones()


def _parse_tz_request(request):
    try:
        data = json.loads(request.body)
        tz = data.get("timezone", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return None, JsonResponse({"ok": False, "error": "bad request"}, status=400)
    if tz not in VALID_TIMEZONES:
        return None, JsonResponse({"ok": False, "error": "unknown timezone"}, status=400)
    return tz, None


@login_required
@require_POST
def set_timezone_auto(request):
    tz, err = _parse_tz_request(request)
    if err:
        return err
    user = request.user
    if user.timezone == "UTC" and not user.timezone_auto_detected:
        user.timezone = tz
        user.timezone_auto_detected = True
        user.save(update_fields=["timezone", "timezone_auto_detected"])
    return JsonResponse({"ok": True, "timezone": user.timezone})


@login_required
@require_POST
def set_timezone_manual(request):
    tz, err = _parse_tz_request(request)
    if err:
        return err
    user = request.user
    user.timezone = tz
    user.timezone_auto_detected = False
    user.save(update_fields=["timezone", "timezone_auto_detected"])
    return JsonResponse({"ok": True, "timezone": user.timezone})
