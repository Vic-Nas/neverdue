# dashboard/templatetags/page_qs.py
from django import template

register = template.Library()


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
