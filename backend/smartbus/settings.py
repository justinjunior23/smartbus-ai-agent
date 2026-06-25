"""
Django settings for smartbus project.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent  # E:\smartbus-ai-agent — one level above backend/,
                                 # where the top-level frontend/ folder actually lives

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-x_xeny9(7&h@hgk$m=bp@p&-e4--q@92z)8&18%8rhc(+i3y36'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'transit',
    'agent',
    # NOTE: 'frontend' is intentionally NOT listed here. It is not a
    # Django app — it has no Python code, no apps.py. It's just a
    # folder of templates/static that Django reaches into via
    # TEMPLATES['DIRS'] and STATICFILES_DIRS below. Adding it to
    # INSTALLED_APPS would crash on startup (no such importable module).
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'smartbus.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # PROJECT_ROOT, not BASE_DIR — frontend/ is a sibling of backend/,
        # not a child of it. Using BASE_DIR here would look inside
        # backend/frontend/ instead of the real top-level frontend/.
        'DIRS': [PROJECT_ROOT / 'frontend' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

STATICFILES_DIRS = [PROJECT_ROOT / 'frontend' / 'static']

WSGI_APPLICATION = 'smartbus.wsgi.application'


# Database

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# Static files

STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],
}

# --- SmartBus / Qwen Cloud config ---
QWEN_API_KEY = os.getenv("DASHSCOPE_API_KEY", os.getenv("QWEN_API_KEY", ""))
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus-latest")