"""
Django settings for juntagrico pulse demo.
"""

import os
from pathlib import Path

from juntagrico import defaults

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('JUNTAGRICO_SECRET_KEY')

DEBUG = os.environ.get('JUNTAGRICO_DEBUG', 'False') == 'True'

if allowed_hosts := os.environ.get('JUNTAGRICO_DOMAINS', False):
    ALLOWED_HOSTS = allowed_hosts.split(',')

ADMINS = (('Admin', os.environ.get('JUNTAGRICO_ADMIN_EMAIL')),)
MANAGERS = ADMINS

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'simple': {'format': '%(levelname)s %(message)s'}},
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        }
    },
    'loggers': {
        '': {
            'handlers': ['console'],
            'level': 'WARNING',
        },
    },
}

# Application definition

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'juntagrico.apps.JuntagricoAdminConfig',
    'impersonate',
    'juntagrico_pulse',
    'juntagrico',
    'crispy_forms',
    'crispy_bootstrap4',
    'adminsortable2',
    'django_select2',
    'polymorphic',
    'import_export',
    'djrichtextfield',
]

ROOT_URLCONF = 'dev.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.contrib.auth.context_processors.auth',
                'django.template.context_processors.debug',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.static',
                'django.template.context_processors.tz',
                'django.template.context_processors.request',
                'django.contrib.messages.context_processors.messages',
                'juntagrico.context_processors.vocabulary',
            ],
            'debug': DEBUG,
        },
    },
]

WSGI_APPLICATION = 'dev.wsgi.application'

# HTTP

MIDDLEWARE = [
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'impersonate.middleware.ImpersonateMiddleware',
    'django.contrib.sites.middleware.CurrentSiteMiddleware',
]

# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': os.environ.get(
            'JUNTAGRICO_DATABASE_ENGINE', 'django.db.backends.sqlite3'
        ),
        'NAME': os.environ.get(
            'JUNTAGRICO_DATABASE_NAME', BASE_DIR / 'juntagrico_pulse.db'
        ),
        'USER': os.environ.get('JUNTAGRICO_DATABASE_USER'),
        'PASSWORD': os.environ.get('JUNTAGRICO_DATABASE_PASSWORD'),
        'HOST': os.environ.get('JUNTAGRICO_DATABASE_HOST', 'localhost'),
        'PORT': os.environ.get('JUNTAGRICO_DATABASE_PORT', False),
    }
}

# Email

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

USE_I18N = True
USE_L10N = True
LANGUAGE_CODE = 'de'
DATE_INPUT_FORMATS = ['%d.%m.%Y']

TIME_ZONE = 'Europe/Zurich'
USE_TZ = True

# Static files (CSS, JavaScript, Images)
# django.contrib.staticfiles
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'static'

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage',
    },
}

# django.contrib.sites Settings

SITE_ID = 1


# django.contrib.auth Settings

AUTHENTICATION_BACKENDS = (
    'juntagrico.util.auth.AuthenticateWithEmail',
    'django.contrib.auth.backends.ModelBackend',
)

LOGIN_REDIRECT_URL = '/'


# impersonate Settings

IMPERSONATE = {
    'REDIRECT_URL': '/my/profile',
}


# crispy_form Settings

CRISPY_TEMPLATE_PACK = 'bootstrap4'


# import_export Settings

IMPORT_EXPORT_EXPORT_PERMISSION_CODE = 'view'


# Rich text editor settings

DJRICHTEXTFIELD_CONFIG = defaults.richtextfield_config(LANGUAGE_CODE)


# juntagrico Settings

ORGANISATION_NAME = 'Juntagrico Zürich'
ORGANISATION_LONG_NAME = 'Juntagrico Zürich [Test]'
ORGANISATION_ADDRESS = {
    'name': '-',
    'street': '-',
    'number': '-',
    'zip': '-',
    'city': '-',
    'extra': '',
}
ORGANISATION_BANK_CONNECTION = {
    'PC': '-',
    'IBAN': '-',
    'BIC': '-',
    'NAME': '-',
    'ESR': '-',
}

ORGANISATION_WEBSITE = {'name': 'juntagrico.org', 'url': 'https://juntagrico.org'}

CONTACTS = {'general': 'test@juntagrico.invalid'}

ENABLE_SHARES = False
