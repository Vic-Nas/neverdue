# billing/tests/settings_test.py
"""
Override settings for billing tests.
DEBUG=True loads dev.env which has the real sk_test_ key and STRIPE_PRICE_ID.
Procrastinate removed — not needed and requires Postgres driver.
"""
from project.settings import *  # noqa: F401,F403

INSTALLED_APPS = [a for a in INSTALLED_APPS if 'procrastinate' not in a]  # noqa: F405

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'

# Match the actual login URL so @login_required redirects agree with tests
LOGIN_URL = '/'