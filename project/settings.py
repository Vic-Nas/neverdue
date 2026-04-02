# project/settings.py
from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY')

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

DOMAIN = os.environ.get('DOMAIN', 'localhost')

ALLOWED_HOSTS = ['localhost', '127.0.0.1', DOMAIN]

CSRF_TRUSTED_ORIGINS = [f'https://{DOMAIN}']

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

AUTH_USER_MODEL = 'accounts.User'

LOGGING = {
    'version': 1,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'loggers': {
        'procrastinate': {
            'handlers': ['console'],
            'level': 'WARNING',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG' if DEBUG else 'WARNING',
    },
}

INSTALLED_APPS = [
    # django.contrib.admin removed — replaced by /staff/ dashboard
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'accounts',
    'billing',
    'dashboard.apps.DashboardConfig',
    'emails',
    'llm',
    'procrastinate.contrib.django',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'project' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'accounts.context_processors.global_settings',
            ],
        },
    },
]

WSGI_APPLICATION = 'project.wsgi.application'

DATABASES = {
    'default': dj_database_url.config(
        default=os.environ.get('DATABASE_URL', f'sqlite:///{BASE_DIR / "db.sqlite3"}')
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'project' / 'static' / 'manual']
STATIC_ROOT = BASE_DIR / 'project' / 'static' / 'cache'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Google OAuth
GOOGLE_CLIENT_ID     = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')

# Resend
RESEND_API_KEY        = os.environ.get('RESEND_API_KEY')
RESEND_FROM_EMAIL     = os.environ.get('RESEND_FROM_EMAIL', f'noreply@{DOMAIN}')
RESEND_WEBHOOK_SECRET = os.environ.get('RESEND_WEBHOOK_SECRET', '')

# LLM
LLM_API_KEY = os.environ.get('LLM_API_KEY')
LLM_MODEL   = os.environ.get('LLM_MODEL', 'claude-sonnet-4-20250514')

# Procrastinate — uses the default Django DB (Postgres). No broker needed.
PROCRASTINATE_ON_APP_READY = None  # tasks auto-discovered via INSTALLED_APPS

# Ads
ADSENSE_CLIENT_ID = os.environ.get('ADSENSE_CLIENT_ID')
ADSENSE_SLOTS     = os.environ.get('ADSENSE_SLOTS', '').split(',')

STRIPE_SECRET_KEY      = os.environ.get('STRIPE_SECRET_KEY')
STRIPE_PRICE_ID        = os.environ.get('STRIPE_PRICE_ID')
STRIPE_WEBHOOK_SECRET  = os.environ.get('STRIPE_WEBHOOK_SECRET')