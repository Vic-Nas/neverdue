import zoneinfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

LANGUAGES = [
    'English', 'Français', 'Español', 'Deutsch',
    'Português', 'Italiano', '中文', '日本語', 'العربية',
]

GCAL_COLORS = [
    {'id': 1,  'name': 'Lavender',  'hex': '#7986cb'},
    {'id': 2,  'name': 'Sage',      'hex': '#33b679'},
    {'id': 3,  'name': 'Grape',     'hex': '#8e24aa'},
    {'id': 4,  'name': 'Flamingo',  'hex': '#e67c73'},
    {'id': 5,  'name': 'Banana',    'hex': '#f6c026'},
    {'id': 6,  'name': 'Tangerine', 'hex': '#f5511d'},
    {'id': 7,  'name': 'Peacock',   'hex': '#039be5'},
    {'id': 8,  'name': 'Graphite',  'hex': '#616161'},
    {'id': 9,  'name': 'Blueberry', 'hex': '#3f51b5'},
    {'id': 10, 'name': 'Basil',     'hex': '#0b8043'},
    {'id': 11, 'name': 'Tomato',    'hex': '#d60000'},
]

VALID_PRIORITY_COLOR_IDS = {c['id'] for c in GCAL_COLORS}
GCAL_COLOR_HEX = {str(c['id']): c['hex'].upper() for c in GCAL_COLORS}


def _parse_priority_color(post, field, default):
    try:
        value = int(post.get(field, default))
    except (ValueError, TypeError):
        return default
    return value if value in VALID_PRIORITY_COLOR_IDS else default


@login_required
def preferences(request):
    if request.method == 'POST':
        language = request.POST.get('language', 'English').strip()
        auto_delete = request.POST.get('auto_delete_past_events') == 'on'
        retention_days = request.POST.get('past_event_retention_days', '30').strip()
        delete_gcal = request.POST.get('delete_from_gcal_on_cleanup') == 'on'
        revoke_on_logout = request.POST.get('revoke_google_on_logout') == 'on'
        timezone_str = request.POST.get('timezone', 'UTC').strip()

        try:
            retention_days = max(1, int(retention_days))
        except (ValueError, TypeError):
            retention_days = 30

        if timezone_str not in zoneinfo.available_timezones():
            timezone_str = 'UTC'

        priority_color_low    = _parse_priority_color(request.POST, 'priority_color_low',    2)
        priority_color_medium = _parse_priority_color(request.POST, 'priority_color_medium',  5)
        priority_color_high   = _parse_priority_color(request.POST, 'priority_color_high',    6)
        priority_color_urgent = _parse_priority_color(request.POST, 'priority_color_urgent', 11)

        request.user.language = language
        request.user.auto_delete_past_events = auto_delete
        request.user.past_event_retention_days = retention_days
        request.user.delete_from_gcal_on_cleanup = delete_gcal
        request.user.revoke_google_on_logout = revoke_on_logout
        request.user.timezone = timezone_str
        request.user.timezone_auto_detected = False
        request.user.priority_color_low    = priority_color_low
        request.user.priority_color_medium = priority_color_medium
        request.user.priority_color_high   = priority_color_high
        request.user.priority_color_urgent = priority_color_urgent
        request.user.save(update_fields=[
            'language', 'auto_delete_past_events', 'past_event_retention_days',
            'delete_from_gcal_on_cleanup', 'revoke_google_on_logout',
            'timezone', 'timezone_auto_detected',
            'priority_color_low', 'priority_color_medium',
            'priority_color_high', 'priority_color_urgent',
        ])
        messages.success(request, 'Preferences saved.')
        return redirect('accounts:preferences')

    user = request.user
    priority_levels = [
        {'label': 'Low',    'field': 'priority_color_low',    'current': user.priority_color_low},
        {'label': 'Medium', 'field': 'priority_color_medium', 'current': user.priority_color_medium},
        {'label': 'High',   'field': 'priority_color_high',   'current': user.priority_color_high},
        {'label': 'Urgent', 'field': 'priority_color_urgent', 'current': user.priority_color_urgent},
    ]

    return render(request, 'accounts/preferences.html', {
        'languages': LANGUAGES,
        'gcal_colors': GCAL_COLORS,
        'priority_levels': priority_levels,
    })
