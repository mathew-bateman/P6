from __future__ import annotations

import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "p6_backup_admin.settings")

app = Celery("p6_backup_admin")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.imports = ("backups.tasks",)
app.autodiscover_tasks()
