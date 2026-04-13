# dashboard/templatetags/page_qs.py
from django import template

register = template.Library()

# gcal color id → hex (mirrors the dict in views/categories.py)
_GCAL_HEX = {
    '1':  '#7986CB', '2':  '#33B679', '3':  '#8E24AA',
    '4':  '#E67C73', '5':  '#F6BF26', '6':  '#F4511E',
    '7':  '#039BE5', '8':  '#3F51B5', '9':  '#0B8043',
    '10': '#D50000', '11': '#616161',
}
_PRIORITY_HEX = {1: '#6366f1', 2: '#f59e0b', 3: '#ef4444', 4: '#dc2626'}


@register.filter
def cat_display_color(category):
    """Return the best available hex color for a category.

    Priority: stored .color field → gcal swatch → priority fallback.
    """
    if category.color:
        return category.color
    if category.gcal_color_id:
        return _GCAL_HEX.get(str(category.gcal_color_id), '')
    return _PRIORITY_HEX.get(category.priority, '#6366f1')


@register.filter
def page_qs(page_num, request):
    """Build query string for a page number, preserving other GET params."""
    params = request.GET.copy()
    params['page'] = page_num
    return params.urlencode()


@register.filter
def page_range_items(page_obj):
    """Return a list of page numbers / ellipsis markers for pagination.

    Each item is a dict: {'type': 'page'|'current'|'ellipsis', 'num': int|None}
    Shows: first, last, and a window of ±2 around current page.
    """
    current = page_obj.number
    total = page_obj.paginator.num_pages
    items = []
    prev_was_ellipsis = False

    for num in range(1, total + 1):
        if num == current:
            items.append({'type': 'current', 'num': num})
            prev_was_ellipsis = False
        elif num == 1 or num == total or (current - 2 <= num <= current + 2):
            items.append({'type': 'page', 'num': num})
            prev_was_ellipsis = False
        elif not prev_was_ellipsis:
            items.append({'type': 'ellipsis', 'num': None})
            prev_was_ellipsis = True

    return items
