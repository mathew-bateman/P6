import uuid

from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class ReportingConfig(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    serve_reports = models.BooleanField(default=False)
    calculation_engine = models.CharField(max_length=12, default="sql", choices=[("sql", "SQL Express staging"), ("postgres", "Native PostgreSQL")])
    manage_settings_in_postgres = models.BooleanField(default=False)
    pending_engine = models.CharField(max_length=12, blank=True, default="")
    sql_parallel_enabled = models.BooleanField(default=False)
    interval_minutes = models.PositiveIntegerField(default=60, validators=[MinValueValidator(15), MaxValueValidator(1440)])
    stale_after_minutes = models.PositiveIntegerField(default=120, validators=[MinValueValidator(15), MaxValueValidator(2880)])
    query_timeout_seconds = models.PositiveIntegerField(default=60, validators=[MinValueValidator(10), MaxValueValidator(300)])
    retained_generations = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(3)])
    changed_by = models.CharField(max_length=150, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [("refresh_reporting", "Can start a reporting refresh")]

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]


class ReportingSource(models.Model):
    SOURCE_CHOICES = [("p6_22", "P6 22"), ("p6_23", "P6 23")]
    code = models.CharField(primary_key=True, max_length=12, choices=SOURCE_CHOICES)
    target = models.ForeignKey("backups.BackupTarget", on_delete=models.PROTECT)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]


class ReportingRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=12, default="running", choices=[("running", "Running"), ("success", "Success"), ("failed", "Failed"), ("skipped", "Skipped")])
    trigger = models.CharField(max_length=20, default="scheduled")
    requested_by = models.CharField(max_length=150, blank=True)
    engine = models.CharField(max_length=12, default="sql")
    operation = models.CharField(max_length=20, default="data_sync")
    progress = models.CharField(max_length=250, default="Starting")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    sources = models.JSONField(default=dict)
    manifest = models.JSONField(default=list)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]


class NativeEngineValidation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=12, default="running")
    baseline_batch = models.UUIDField(null=True, blank=True)
    settings_hash = models.CharField(max_length=64, blank=True)
    engine_revision = models.CharField(max_length=64, blank=True)
    requested_by = models.CharField(max_length=150, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    progress = models.CharField(max_length=250, blank=True)
    source_schemas = models.JSONField(default=dict)
    results = models.JSONField(default=dict)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]


class NativeSettingsCheckpoint(models.Model):
    """Portable recovery copy; the authoritative versioned settings live in PG."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    payload = models.JSONField(default=dict)
    sha256 = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    offsite_backed_up_at = models.DateTimeField(null=True, blank=True)
    offsite_sha256 = models.CharField(max_length=64, blank=True)
    offsite_item_id = models.CharField(max_length=250, blank=True)
    offsite_error = models.TextField(blank=True)


class SQLParallelRun(models.Model):
    """Independent audit for the retained P6 22 SQL reporting calculations."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.OneToOneField(ReportingRun, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=12, default="queued")
    trigger = models.CharField(max_length=20, default="scheduled")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    postgres_config_version_id = models.BigIntegerField(null=True, blank=True)
    settings_hash = models.CharField(max_length=64, blank=True)
    sql_config_version_id = models.BigIntegerField(null=True, blank=True)
    sql_check_run_id = models.BigIntegerField(null=True, blank=True)
    processed_project_count = models.PositiveIntegerField(null=True, blank=True)
    progress = models.CharField(max_length=250, default="Queued for SQL reporting refresh")
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
