import pytest
from emails.tasks.helpers import _check_sender_rules, _load_user
from emails.models import ScanJob
from dashboard.models import Rule


@pytest.mark.django_db
class TestCheckSenderRules:
    def test_no_rules_passes(self, user):
        blocked, _ = _check_sender_rules(user, 'any@example.com')
        assert blocked is False

    def test_block_rule(self, user):
        Rule.objects.create(user=user, rule_type='sender', pattern='@spam.com', action='block')
        blocked, note = _check_sender_rules(user, 'foo@spam.com')
        assert blocked is True
        assert 'blocked' in note

    def test_allow_rule_passes_match(self, user):
        Rule.objects.create(user=user, rule_type='sender', pattern='@good.com', action='allow')
        blocked, _ = _check_sender_rules(user, 'a@good.com')
        assert blocked is False

    def test_allow_rule_blocks_non_match(self, user):
        Rule.objects.create(user=user, rule_type='sender', pattern='@good.com', action='allow')
        blocked, _ = _check_sender_rules(user, 'a@other.com')
        assert blocked is True


@pytest.mark.django_db
class TestLoadUser:
    def test_found(self, user):
        job = ScanJob.objects.create(user=user, source='email')
        result = _load_user(user.pk, job.pk)
        assert result.pk == user.pk

    def test_not_found(self, user):
        job = ScanJob.objects.create(user=user, source='email')
        result = _load_user(99999, job.pk)
        assert result is None
        job.refresh_from_db()
        assert job.status == ScanJob.STATUS_FAILED
