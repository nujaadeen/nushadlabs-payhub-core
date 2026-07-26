"""
Django settings for payhub project.

------------------------------------------------------------------------
DELIBERATE GAPS IN THIS PHASE (Phase 0 - scaffolding only)
------------------------------------------------------------------------
This project intentionally skips several things a "real" project would
have from day one. Each is a conscious decision for this phase, not an
oversight - noted here so nobody mistakes it for sloppiness later:

  - No authentication of any kind (no login, no tokens, no sessions used
    for API auth). Deferred until a later phase once the core payment
    flows exist to actually protect.
  - No DRF permission classes (every endpoint is AllowAny). Same reason -
    there's nothing worth gating yet, and adding auth now would slow down
    iterating on the data model.
  - Credential fields (Stripe/Adyen API keys) are stored as plain-text
    CharFields, not encrypted at rest. Deferred until we pick an
    encryption-at-rest approach; each credential field has its own
    "stored raw for now, encrypt later" comment as a reminder.
------------------------------------------------------------------------

For more information on this file, see
https://docs.djangoproject.com/en/6.0/topics/settings/
"""

from pathlib import Path

import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# django-environ reads settings (DB credentials, secret keys, provider API
# keys in later phases) out of a `.env` file instead of us hardcoding them
# in this settings.py. We do this so:
#   1. Secrets never get committed to git (.env is in .gitignore; only
#      .env.example, with placeholder values, is committed).
#   2. The same codebase can run against different databases/credentials
#      in different environments (local dev vs CI vs someone else's
#      machine) just by swapping the .env file, with no code changes.
env = environ.Env()
# Look for a `.env` file in the project root (next to manage.py) and load
# any KEY=value pairs from it into the environment, if the file exists.
environ.Env.read_env(BASE_DIR / ".env")

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-only-change-me")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env.bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    # Local apps
    "tenants",
    "payments_core",
    "payments_stripe",
    "payments_adyen",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "payhub.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "payhub.wsgi.application"


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases
#
# Credentials come from environment variables (via django-environ) rather
# than being hardcoded here, so that:
#   - this file can be committed to git without leaking a real DB password
#   - each developer/environment can point at their own database by just
#     changing their local `.env`, with zero code changes
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", default="payhub"),
        "USER": env("DB_USER", default="payhub"),
        "PASSWORD": env("DB_PASSWORD", default="payhub"),
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Django REST Framework
# https://www.django-rest-framework.org/api-guide/settings/
#
# This is what "no auth for now" looks like in DRF specifically:
#   - DEFAULT_AUTHENTICATION_CLASSES: [] means DRF won't try to identify
#     *who* is calling (no session auth, no token auth, nothing) - every
#     request is treated as anonymous.
#   - DEFAULT_PERMISSION_CLASSES: [AllowAny] means DRF won't check *what*
#     an (anonymous) caller is allowed to do - every request is allowed
#     through, on every endpoint, unless a specific view overrides this.
# Both are deliberately wide open per the project-wide "no auth yet" gap
# noted at the top of this file.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
}
