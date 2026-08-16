from django.contrib import admin

from backups.models import BackupRun, BackupTarget


@admin.register(BackupTarget)
class BackupTargetAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "enabled", "sql_host", "sql_port", "sql_database", "backup_schedule")
    list_filter = ("enabled", "use_compression", "verify_sharepoint_upload")
    search_fields = ("name", "slug", "sql_database", "sharepoint_folder")
    readonly_fields = ("created_at", "updated_at")


@admin.register(BackupRun)
class BackupRunAdmin(admin.ModelAdmin):
    list_display = ("target", "operation", "status", "trigger", "backup_name", "started_at", "finished_at")
    list_filter = ("operation", "status", "trigger", "target")
    search_fields = ("backup_name", "plain_filename", "encrypted_filename", "error_message")
    readonly_fields = ("started_at", "finished_at")
