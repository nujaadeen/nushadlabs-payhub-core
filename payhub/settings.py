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
  - django.contrib.admin, django.contrib.auth, django.contrib.sessions,
    and django.contrib.messages are NOT installed (see INSTALLED_APPS
    below) - this project has no logins, no admin usage, and no Django
    "flash messages" anywhere, so those apps would only exist to create
    database tables (auth_user, django_session, django_admin_log, ...)
    this project never reads from. If you need to inspect data during
    development, use `python manage.py shell` or connect directly with a
    Postgres client (e.g. `psql`) - not the admin app.
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
    # django.contrib.admin, django.contrib.auth, django.contrib.sessions,
    # and django.contrib.messages have been DELIBERATELY removed - see the
    # "DELIBERATE GAPS" note at the top of this file. This project has no
    # authentication, no admin usage, and no Django "flash messages"
    # anywhere, so those four apps would exist ONLY to create tables we'd
    # never use (auth_user, auth_group, django_session, django_admin_log,
    # ...) - Django's `migrate` command creates a table for every model in
    # every INSTALLED_APPS entry, whether or not the project code ever
    # touches it. Removing the app is what stops those tables from being
    # created at all, rather than just leaving them empty.
    #
    # django.contrib.contenttypes is ALSO gone, even though it's a much
    # lower-level app than the four above - it's the one Django/DRF
    # internals are most likely to quietly assume exists. It genuinely
    # CAN be removed here, but only because of the REST_FRAMEWORK ->
    # UNAUTHENTICATED_USER override below: DRF evaluates `request.user` on
    # every request, which by default imports
    # "django.contrib.auth.models.AnonymousUser" - and importing that
    # module defines several model classes (ContentType, Permission,
    # Group, User, ...), each of which needs its own app to be a real,
    # registered INSTALLED_APPS entry. Without the UNAUTHENTICATED_USER
    # override, removing contenttypes (or auth) crashes EVERY request with
    # a 500 RuntimeError - confirmed by actually hitting that crash while
    # working this out, not guessed. With the override in place, DRF never
    # imports django.contrib.auth.models at all, so neither app is needed.
    #
    # django.contrib.staticfiles stays, though - unlike contenttypes, this
    # one has an observable effect we actually want: it's what makes
    # `runserver` serve DRF's browsable API CSS (bootstrap.min.css etc.)
    # from STATIC_URL in development. Removing it was tested too - the
    # browsable API's HTML still renders fine without it, but every CSS
    # file 404s, so the page loads completely unstyled.
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    # corsheaders (django-cors-headers) - lets demo.html, served from a
    # different origin/port than Django (e.g. http://127.0.0.1:5500 via
    # VS Code's "Live Server", or http://localhost:8080 via `python -m
    # http.server`), actually receive responses from this API. Without it,
    # the BROWSER blocks JavaScript from reading the response, even though
    # Django processed the request just fine server-side - CORS protects
    # "can this website's JS read the response", not "can this request
    # reach the server at all". There's one important exception, and it's
    # exactly what demo.html hits: for a POST with a JSON body (like every
    # POST this demo page makes), the browser sends a "preflight" OPTIONS
    # request FIRST to ask permission, and only sends the real POST at all
    # if that preflight succeeds - so for THIS project's actual traffic,
    # a disallowed origin really does mean the real request never leaves
    # the browser, which is what surfaces as "Failed to fetch" client-side.
    # See CORS_ALLOWED_ORIGINS below for exactly which origins we allow.
    "corsheaders",
    # Local apps
    "tenants",
    "payments_core",
    "payments_stripe",
    "payments_adyen",
]

# Deliberately trimmed to the minimum needed for a plain JSON API project
# with no sessions, no auth, and no admin - each entry below still does
# something for us even without those apps; the three we removed
# (SessionMiddleware, AuthenticationMiddleware, MessageMiddleware) existed
# ONLY to support django.contrib.sessions/auth/messages, which are no
# longer installed above, so they'd have nothing to do (Authentication
# Middleware's whole job is setting `request.user` from a session that
# SessionMiddleware attaches - remove one and the other has nothing to
# read; remove both and neither has a reason to run).
#
# Middleware order matters in Django generally: each request passes
# through this list TOP TO BOTTOM on the way in, then BOTTOM TO TOP on the
# way back out (it's a set of nested layers, not a flat sequence) - so
# something near the top of this list gets to see/modify the request
# first, and gets to see/modify the RESPONSE last, wrapping everything
# below it. CorsMiddleware specifically needs to run early (before
# CommonMiddleware) because it has to be able to add CORS headers to
# EVERY response this project sends, including ones other middleware
# might short-circuit or redirect - if it ran late and something earlier
# in the chain returned a response first, CORS headers would never get
# added to that response, and the browser would still block it.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# CORS_ALLOWED_ORIGINS - the list of origins (scheme + host + port, e.g.
# "http://127.0.0.1:5500") that are allowed to make cross-origin requests
# to this API from a browser. This is deliberately a small, EXPLICIT
# allowlist read from an env var - NOT `CORS_ALLOW_ALL_ORIGINS = True`.
# Even though this whole project has no authentication or permission
# checks (so there's no *secret* a stray origin could steal), normalizing
# "just allow every origin" as an easy fix is a bad habit to practice even
# in a learning project - CORS exists to answer "which websites' JavaScript
# can talk to this API from a user's browser", a genuinely different
# question from "who's allowed to use this API" (which is what
# authentication/permissions answer - and which this project still doesn't
# have, on purpose - see the "DELIBERATE GAPS" note at the top of this
# file). Being deliberate about the allowlist here costs us nothing and
# keeps that distinction real instead of papering over it.
#
# Defaults cover the two most common ways to serve demo.html locally:
# VS Code's "Live Server" extension (port 5500) and Python's
# `python -m http.server` (port 8080), on both 127.0.0.1 and localhost
# (browsers treat these as different origins even though they resolve to
# the same machine). If you serve demo.html from a different port, add it
# to CORS_ALLOWED_ORIGINS in your own .env and restart the Django server -
# see demo/README.md.
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ],
)

ROOT_URLCONF = "payhub.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            # The auth/messages context processors that Django's startproject
            # template normally puts here are gone too, and had to be -
            # they're not just unused without those apps installed, they'd
            # actively BREAK every template render: `auth`'s context
            # processor reads `request.user`, which only exists because
            # AuthenticationMiddleware puts it there on every request (see
            # MIDDLEWARE above) - remove that middleware (as we did) and
            # `request.user` no longer exists, so this context processor
            # would raise an AttributeError the next time ANY template
            # renders. That's not just an academic risk here: DRF's
            # "browsable API" (the nice HTML page you get visiting an
            # endpoint in a browser instead of curl) renders a real Django
            # template, so it would hit this exact crash on every request
            # if we left these in.
            "context_processors": [
                "django.template.context_processors.request",
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


# AUTH_PASSWORD_VALIDATORS (Django's startproject default here) has been
# removed entirely, not just left empty - it only ever existed to validate
# passwords for django.contrib.auth's User model, which we don't have
# installed (see INSTALLED_APPS above). Keeping it around, pointing at
# validator classes from an app we've deliberately removed, would be dead
# configuration that misleads a future reader into thinking this project
# does password-based auth somewhere.


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
#   - UNAUTHENTICATED_USER: None - this one is NOT optional once
#     django.contrib.auth is removed from INSTALLED_APPS (see above).
#     DRF's default for this setting is the STRING
#     "django.contrib.auth.models.AnonymousUser", and DRF evaluates it -
#     importing and instantiating that class - on every single request, to
#     populate `request.user`, regardless of DEFAULT_AUTHENTICATION_CLASSES
#     being empty. Importing django.contrib.auth.models defines several
#     model classes (Permission, Group, User, ...), and Django's model
#     metaclass requires every model to belong to a real, registered
#     INSTALLED_APPS entry - which auth no longer is - so without this
#     override, EVERY request crashes with a 500 RuntimeError the moment
#     DRF tries to check who's calling. Setting this to `None` (an
#     officially supported DRF setting, not a hack) tells DRF to just use
#     `request.user = None` for every request instead - which is arguably
#     more honest than a fake AnonymousUser anyway, since this project
#     doesn't have a concept of "user" at all.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "UNAUTHENTICATED_USER": None,
}
