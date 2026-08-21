from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from django.db import models
from django.urls import reverse


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,\n;]+", value or "") if item.strip()]


class BackupTarget(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=False)

    sql_host = models.CharField(max_length=255, default="host.docker.internal")
    sql_port = models.PositiveIntegerField(default=1433)
    sql_database = models.CharField(max_length=128)
    sql_username = models.CharField(max_length=128, default="sa")
    sql_password_env = models.CharField("SQL password", max_length=128, blank=True)
    odbc_driver = models.CharField(max_length=120, default="ODBC Driver 18 for SQL Server")
    encrypt_connection = models.BooleanField(default=True)
    trust_server_certificate = models.BooleanField(default=True)

    sql_backup_dir = models.CharField(max_length=255, default="/var/backups")
    app_backup_dir = models.CharField(max_length=500)
    sql_data_dir = models.CharField(max_length=255, default="/var/database")
    data_file_path = models.CharField(max_length=500, blank=True)
    log_file_path = models.CharField(max_length=500, blank=True)

    backup_schedule = models.CharField(max_length=80, default="0 2 * * 0")
    sharepoint_site = models.CharField(max_length=255, default="root")
    sharepoint_folder = models.CharField(max_length=500, default="P6 Backups")
    notification_emails = models.TextField(blank=True)
    retention_daily = models.PositiveIntegerField(default=14)
    retention_weekly = models.PositiveIntegerField(default=8)
    retention_monthly = models.PositiveIntegerField(default=12)
    use_compression = models.BooleanField(default=False)
    verify_sharepoint_upload = models.BooleanField(default=False)
    keep_local_files = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("backup_target_detail", kwargs={"slug": self.slug})

    @property
    def app_backup_path(self) -> Path:
        return Path(self.app_backup_dir)

    @property
    def sql_password(self) -> str:
        return (self.sql_password_env or "").strip()

    @property
    def notification_email_list(self) -> list[str]:
        return _split_csv(self.notification_emails)

    @property
    def resolved_data_file_path(self) -> str:
        if self.data_file_path:
            return self.data_file_path
        return f"{self.sql_data_dir.rstrip('/')}/{self.sql_database}_DAT.MDF"

    @property
    def resolved_log_file_path(self) -> str:
        if self.log_file_path:
            return self.log_file_path
        return f"{self.sql_data_dir.rstrip('/')}/{self.sql_database}_LOG.LDF"


class BackupRun(models.Model):
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_DISABLED = "disabled"

    TRIGGER_SCHEDULED = "scheduled"
    TRIGGER_MANUAL = "manual"
    TRIGGER_COMMAND = "command"

    OPERATION_BACKUP = "backup"
    OPERATION_RESTORE = "restore"

    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
        (STATUS_DISABLED, "Disabled"),
    ]
    TRIGGER_CHOICES = [
        (TRIGGER_SCHEDULED, "Scheduled"),
        (TRIGGER_MANUAL, "Manual"),
        (TRIGGER_COMMAND, "Command"),
    ]
    OPERATION_CHOICES = [
        (OPERATION_BACKUP, "Backup"),
        (OPERATION_RESTORE, "Restore"),
    ]

    target = models.ForeignKey(BackupTarget, on_delete=models.CASCADE, related_name="runs")
    operation = models.CharField(max_length=16, choices=OPERATION_CHOICES, default=OPERATION_BACKUP)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    trigger = models.CharField(max_length=16, choices=TRIGGER_CHOICES, default=TRIGGER_SCHEDULED)

    backup_name = models.CharField(max_length=180, blank=True)
    plain_filename = models.CharField(max_length=220, blank=True)
    encrypted_filename = models.CharField(max_length=240, blank=True)
    manifest_filename = models.CharField(max_length=260, blank=True)
    plaintext_size_bytes = models.BigIntegerField(default=0)
    encrypted_size_bytes = models.BigIntegerField(default=0)
    plaintext_sha256 = models.CharField(max_length=64, blank=True)
    encrypted_sha256 = models.CharField(max_length=64, blank=True)
    sharepoint_folder = models.CharField(max_length=500, blank=True)
    sharepoint_backup_item_id = models.CharField(max_length=255, blank=True)
    sharepoint_manifest_item_id = models.CharField(max_length=255, blank=True)
    sharepoint_backup_web_url = models.URLField(max_length=1000, blank=True)
    sharepoint_manifest_web_url = models.URLField(max_length=1000, blank=True)
    restore_source_filename = models.CharField(max_length=260, blank=True)
    error_message = models.TextField(blank=True)

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["target", "-started_at"]),
            models.Index(fields=["status", "-started_at"]),
        ]

    def __str__(self) -> str:
        return self.backup_name or f"{self.get_operation_display()} run {self.pk}"

    @property
    def backup_timestamp_display(self) -> str:
        """Return the timestamp encoded in a standard backup filename."""

        filename = self.encrypted_filename or self.restore_source_filename
        match = re.search(r"_backup_(\d{8})_(\d{6})\.bak(?:\.enc)?$", filename)
        if not match:
            return ""
        try:
            return datetime.strptime("_".join(match.groups()), "%Y%m%d_%H%M%S").strftime("%d %b %Y, %H:%M")
        except ValueError:
            return ""

class UserdataPruneRun(models.Model):
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"

    TRIGGER_SCHEDULED = "scheduled"
    TRIGGER_MANUAL = "manual"

    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
    ]
    TRIGGER_CHOICES = [
        (TRIGGER_SCHEDULED, "Scheduled"),
        (TRIGGER_MANUAL, "Manual"),
    ]

    trigger = models.CharField(max_length=16, choices=TRIGGER_CHOICES, default=TRIGGER_SCHEDULED)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    threshold_mb = models.PositiveIntegerField(default=5)
    targets_scanned = models.PositiveIntegerField(default=0)
    users_pruned_count = models.PositiveIntegerField(default=0)
    total_bytes_freed = models.BigIntegerField(default=0)
    log_output = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["-started_at"]),
            models.Index(fields=["status", "-started_at"]),
        ]

    def __str__(self) -> str:
        return f"USERDATA Prune Run #{self.pk} ({self.get_trigger_display()})"

    @property
    def total_mb_freed(self) -> float:
        return round(self.total_bytes_freed / 1024.0 / 1024.0, 2)


class UserdataPruneItem(models.Model):
    run = models.ForeignKey(UserdataPruneRun, on_delete=models.CASCADE, related_name="items")
    target_name = models.CharField(max_length=120)
    user_name = models.CharField(max_length=128)
    actual_name = models.CharField(max_length=128, blank=True)
    original_bytes = models.BigIntegerField(default=0)
    pruned_bytes = models.BigIntegerField(default=0)
    bytes_freed = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-bytes_freed"]

    def __str__(self) -> str:
        return f"{self.user_name} @ {self.target_name} ({self.mb_freed} MB freed)"

    @property
    def original_mb(self) -> float:
        return round(self.original_bytes / 1024.0 / 1024.0, 2)

    @property
    def pruned_mb(self) -> float:
        return round(self.pruned_bytes / 1024.0 / 1024.0, 2)

    @property
    def mb_freed(self) -> float:
        return round(self.bytes_freed / 1024.0 / 1024.0, 2)


class DatabaseMaintenanceRun(models.Model):
    TYPE_INDEX_DEFRAG = "index_defrag"
    TYPE_STALE_SESSION = "stale_session"
    TYPE_LOG_PRUNING = "log_pruning"
    TYPE_USERDATA_PRUNE = "userdata_prune"
    TYPE_STATISTICS = "statistics"
    TYPE_HEALTH_CHECK = "health_check"
    TYPE_P6_BACKGROUND_HEALTH = "p6_background_health"

    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"

    TRIGGER_SCHEDULED = "scheduled"
    TRIGGER_MANUAL = "manual"

    TYPE_CHOICES = [
        (TYPE_INDEX_DEFRAG, "Index Defragmentation & Stats"),
        (TYPE_STALE_SESSION, "Stale Session & Lock Purge"),
        (TYPE_LOG_PRUNING, "Audit Log Retention Pruning"),
        (TYPE_USERDATA_PRUNE, "USERDATA Bloat Cleanup"),
        (TYPE_STATISTICS, "SQL Statistics Refresh"),
        (TYPE_HEALTH_CHECK, "Database Physical Integrity Check"),
        (TYPE_P6_BACKGROUND_HEALTH, "P6 Native Background Health"),
    ]
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
    ]
    TRIGGER_CHOICES = [
        (TRIGGER_SCHEDULED, "Scheduled"),
        (TRIGGER_MANUAL, "Manual"),
    ]

    run_type = models.CharField(max_length=32, choices=TYPE_CHOICES, default=TYPE_USERDATA_PRUNE)
    trigger = models.CharField(max_length=16, choices=TRIGGER_CHOICES, default=TRIGGER_SCHEDULED)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    targets_scanned = models.PositiveIntegerField(default=0)
    items_processed_count = models.PositiveIntegerField(default=0)
    metrics_summary = models.TextField(blank=True)
    log_output = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["run_type", "-started_at"]),
            models.Index(fields=["status", "-started_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_run_type_display()} Run #{self.pk} ({self.get_status_display()})"


class DatabaseMaintenanceItem(models.Model):
    run = models.ForeignKey(DatabaseMaintenanceRun, on_delete=models.CASCADE, related_name="details")
    target_name = models.CharField(max_length=120)
    item_label = models.CharField(max_length=255)
    metric_before = models.CharField(max_length=120, blank=True)
    metric_after = models.CharField(max_length=120, blank=True)
    detail_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.item_label} @ {self.target_name}"
