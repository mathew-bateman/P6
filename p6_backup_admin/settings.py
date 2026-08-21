from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [
        item.strip()
        for item in os.getenv(name, default).replace(";", ",").split(",")
        if item.strip()
    ]


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-p6-backup-admin-dev-key")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,p6-backup-admin")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.humanize",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_celery_beat",
    "graph",
    "backups",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "p6_backup_admin.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "backups.context_processors.schedule_quality_access",
            ],
        },
    }
]

WSGI_APPLICATION = "p6_backup_admin.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.getenv("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = os.getenv("TIME_ZONE", "Europe/London")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://p6-backup-redis:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

BACKUP_STAGING_ROOT = Path(os.getenv("BACKUP_STAGING_ROOT", str(BASE_DIR / "backup-temp")))
BACKUP_KEEP_LOCAL = env_bool("BACKUP_KEEP_LOCAL", False)
BACKUP_VERIFY_SHAREPOINT_UPLOAD = env_bool("BACKUP_VERIFY_SHAREPOINT_UPLOAD", False)
BACKUP_ENCRYPTION_KEYS = json.loads(os.getenv("BACKUP_ENCRYPTION_KEYS", "[]"))

GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "")
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "")
GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET", "")
GRAPH_MAIL_SENDER = os.getenv("GRAPH_MAIL_SENDER", "")

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "p6-backups@example.local")

P6_DEFAULT_SQL_HOST = os.getenv("P6_DEFAULT_SQL_HOST", "host.docker.internal")
P6_AXIALP6_SQL_PORT = int(os.getenv("P6_AXIALP6_SQL_PORT", "1466"))
P6_P62212_SQL_PORT = int(os.getenv("P6_P62212_SQL_PORT", "1433"))

P6_SCHEDULE_QUALITY_REFRESH_ENABLED = env_bool("P6_SCHEDULE_QUALITY_REFRESH_ENABLED", True)
P6_SCHEDULE_QUALITY_REFRESH_SCHEDULE = os.getenv(
    "P6_SCHEDULE_QUALITY_REFRESH_SCHEDULE",
    "*/15 * * * *",
)
P6_SCHEDULE_QUALITY_REFRESH_PROJ_ID = os.getenv("P6_SCHEDULE_QUALITY_REFRESH_PROJ_ID", "")
P6_SCHEDULE_QUALITY_PROFILE_CODE = os.getenv("P6_SCHEDULE_QUALITY_PROFILE_CODE", "default")
P6_SCHEDULE_QUALITY_EDITOR_GROUP = os.getenv(
    "P6_SCHEDULE_QUALITY_EDITOR_GROUP",
    "Schedule Quality Editors",
)
P6_SCHEDULE_QUALITY_REPORT_GROUP = os.getenv(
    "P6_SCHEDULE_QUALITY_REPORT_GROUP",
    "ScheduleQuality",
)
P6_SCHEDULE_QUALITY_SQL_HOST = os.getenv("P6_SCHEDULE_QUALITY_SQL_HOST", P6_DEFAULT_SQL_HOST)
P6_SCHEDULE_QUALITY_SQL_PORT = int(os.getenv("P6_SCHEDULE_QUALITY_SQL_PORT", str(P6_P62212_SQL_PORT)))
P6_SCHEDULE_QUALITY_SQL_DATABASE = os.getenv("P6_SCHEDULE_QUALITY_SQL_DATABASE", "P62212_1")
P6_SCHEDULE_QUALITY_SQL_USERNAME = os.getenv("P6_SCHEDULE_QUALITY_SQL_USERNAME", "admin")
P6_SCHEDULE_QUALITY_SQL_PASSWORD = os.getenv(
    "P6_SCHEDULE_QUALITY_SQL_PASSWORD",
    os.getenv("P6_P62212_SQL_PASSWORD", ""),
)
P6_SCHEDULE_QUALITY_SQL_DRIVER = os.getenv(
    "P6_SCHEDULE_QUALITY_SQL_DRIVER",
    "ODBC Driver 18 for SQL Server",
)
P6_SCHEDULE_QUALITY_SQL_ENCRYPT = env_bool("P6_SCHEDULE_QUALITY_SQL_ENCRYPT", True)
P6_SCHEDULE_QUALITY_SQL_TRUST_CERT = env_bool("P6_SCHEDULE_QUALITY_SQL_TRUST_CERT", True)
