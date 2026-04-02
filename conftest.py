import os
os.environ.setdefault('DEBUG', 'True')

import django
from django.conf import settings

if not settings.configured:
    os.environ['DJANGO_SETTINGS_MODULE'] = 'project.settings'
    django.setup()

import pytest
from django.utils import timezone
from accounts.models import User


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username='testuser', password='testpass',
        email='test@example.com', timezone='America/Toronto',
        language='English',
    )


@pytest.fixture
def pro_user(db, user):
    from billing.models import Subscription
    Subscription.objects.create(
        user=user, stripe_customer_id='cus_test',
        stripe_subscription_id='sub_test', status='active',
    )
    user.refresh_from_db()
    return user


@pytest.fixture
def client(db):
    from django.test import Client
    return Client()


@pytest.fixture
def auth_client(client, user):
    client.force_login(user)
    return client
