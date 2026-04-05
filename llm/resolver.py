# llm/resolver.py
from dashboard.models import Category, Rule

# Keywords that suggest a higher priority, mapped to priority level
_PRIORITY_HINTS: list[tuple[int, list[str]]] = [
    (4, ['exam', 'examen', 'final', 'midterm', 'deadline', 'due', 'urgent', 'overdue']),
    (3, ['assignment', 'devoir', 'quiz', 'test', 'lab', 'projet', 'project', 'meeting', 'réunion']),
    (2, ['cours', 'course', 'lecture', 'class', 'seminar', 'tutorial', 'work', 'travail']),
]

# Sentinel returned (as the first element of a tuple) by resolve_category when a
# discard rule matches.  Callers must check:
#   result = resolve_category(...)
#   if isinstance(result, tuple) and result[0] is DISCARD:
#       _, rule = result   # rule is the Rule instance that fired
DISCARD = object()


def _infer_priority(hint: str) -> int:
    """
    Infer a priority level (1–4) from a category hint string.
    Falls back to 2 (Medium) instead of the model default of 1 (Low).
    """
    lower = hint.lower()
    for priority, keywords in _PRIORITY_HINTS:
        if any(kw in lower for kw in keywords):
            return priority
    return 2


def collect_prompt_injections(user, sender: str = '') -> str:
    """
    Return the combined user-defined LLM instructions applicable to this email.

    A prompt rule with no pattern always applies.
    A prompt rule with a pattern only applies when the pattern is a substring
    of the sender address (case-insensitive).

    Returns a newline-joined string, or '' if no rules match.
    """
    injections = []
    sender_lower = sender.lower() if sender else ''
    for rule in Rule.objects.filter(user=user, rule_type=Rule.TYPE_PROMPT):
        text = rule.prompt_text.strip()
        if not text:
            continue
        if rule.pattern:
            if sender_lower and rule.pattern.lower() in sender_lower:
                injections.append(text)
        else:
            injections.append(text)
    return '\n'.join(injections)


def resolve_category(user, event: dict, sender: str = '') -> 'Category | tuple | None':
    """
    Resolve the best category for an event, or signal that it should be discarded.

    Returns:
      - Category instance          → assign this category
      - (DISCARD, Rule) tuple      → skip this event entirely (discard rule matched);
                                     the Rule instance identifies which rule fired
      - None                       → leave uncategorized (caller assigns Uncategorized)

    Priority:
      1. Sender rules — pattern is substring of sender address
      2. Keyword rules — pattern is substring of event title + description
      3. LLM category_hint matched against existing categories (case-insensitive)
      4. LLM hint used to get_or_create a new category
      5. None
    """
    rules = Rule.objects.filter(user=user).select_related('category').order_by('created_at')

    # 1. Sender rules
    sender_lower = sender.lower() if sender else ''
    for rule in rules.filter(rule_type=Rule.TYPE_SENDER):
        if rule.pattern and sender_lower and rule.pattern.lower() in sender_lower:
            if rule.action == Rule.ACTION_DISCARD:
                return DISCARD, rule
            if rule.action == Rule.ACTION_CATEGORIZE and rule.category_id:
                return rule.category

    # 2. Keyword rules
    searchable = f"{event.get('title', '')} {event.get('description', '')}".lower()
    for rule in rules.filter(rule_type=Rule.TYPE_KEYWORD):
        if rule.pattern and rule.pattern.lower() in searchable:
            if rule.action == Rule.ACTION_DISCARD:
                return DISCARD, rule
            if rule.action == Rule.ACTION_CATEGORIZE and rule.category_id:
                return rule.category

    # 3. Match hint against existing category names
    hint = event.get('category_hint', '').strip()
    if hint:
        existing = Category.objects.filter(user=user)
        for cat in existing:
            if cat.name.lower() == hint.lower():
                return cat

        # 4. Create new category from hint
        cat, _ = Category.objects.get_or_create(
            user=user,
            name=hint.capitalize(),
            defaults={
                'reminders': [],
                'priority': _infer_priority(hint),
            },
        )
        return cat

    return None