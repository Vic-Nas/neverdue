# billing/tests/settings_test.py
"""
Test settings overlay.
Usage:
    DEBUG=True DATABASE_URL="" python manage.py test billing.tests \
        --settings=billing.tests.settings_test

- DEBUG=True loads dev.env (picks up STRIPE_SECRET_KEY, STRIPE_PRICE_ID, etc.)
- DATABASE_URL="" makes dj-database-url fall back to SQLite
- Procrastinate is removed from INSTALLED_APPS to avoid psycopg/Postgres dependency
"""
from project.settings import *  # noqa: F401,F403

# Remove procrastinate — not needed for billing tests, requires Postgres
INSTALLED_APPS = [app for app in INSTALLED_APPS  # noqa: F405
                  if 'procrastinate' not in app]

# Ensure SQLite (DATABASE_URL="" in env does this, but belt-and-suspenders)
import os as _os
if not _os.environ.get('DATABASE_URL'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'test_billing.sqlite3',  # noqa: F405
        }
    }

# Speed up password hashing in tests
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Disable whitenoise manifest in tests
STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
