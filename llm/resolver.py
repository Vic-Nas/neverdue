# llm/resolver.py
from dashboard.models import Category, Rule


def resolve_category(user, event: dict, sender: str = '') -> Category | None:
    """
    Resolve the best category for an event.
    Priority:
    1. User rules (sender or keyword match)
    2. LLM category_hint matched against existing categories
    3. LLM hint used to create a new category
    4. None (uncategorized)
    """
    rules = Rule.objects.filter(user=user).select_related('category')

    # 1. Rule match
    for rule in rules:
        if rule.sender and sender and rule.sender.lower() in sender.lower():
            return rule.category
        if rule.keyword:
            searchable = f"{event.get('title', '')} {event.get('description', '')}".lower()
            if rule.keyword.lower() in searchable:
                return rule.category

    # 2. Match hint against existing category names
    hint = event.get('category_hint', '').strip()
    if hint:
        existing = Category.objects.filter(user=user)
        for cat in existing:
            if cat.name.lower() == hint.lower():
                return cat

        # 3. Create new category from hint
        cat = Category.objects.create(
            user=user,
            name=hint.capitalize(),
            reminders=[],
        )
        return cat

    return None