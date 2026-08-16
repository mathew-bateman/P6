from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from backups.models import BackupTarget


DEFAULT_TARGETS = [
    {
        "slug": "axialp6",
        "name": "AxialP6",
        "description": "Main Axial P6 SQL Server database.",
        "sql_database": "Axial",
        "sql_password": "",
        "sql_port_setting": "P6_AXIALP6_SQL_PORT",
        "app_backup_dir": "/mnt/p6-backups/axialp6",
        "sharepoint_folder": "P6 Backups/AxialP6",
        "data_file_path": "/var/database/Axial_DAT.MDF",
        "log_file_path": "/var/database/Axial_LOG.LDF",
    },
    {
        "slug": "axial-training",
        "name": "Axial Training",
        "description": "Training database inside the AxialP6 SQL Server instance.",
        "sql_database": "Axial_Training",
        "sql_password": "",
        "sql_port_setting": "P6_AXIALP6_SQL_PORT",
        "app_backup_dir": "/mnt/p6-backups/axialp6",
        "sharepoint_folder": "P6 Backups/Axial Training",
        "data_file_path": "/var/database/Axial_Training_DAT.MDF",
        "log_file_path": "/var/database/Axial_Training_LOG.LDF",
    },
    {
        "slug": "p62212",
        "name": "P62212",
        "description": "P62212 SQL Server database environment.",
        "sql_database": "Axial",
        "sql_password": "",
        "sql_port_setting": "P6_P62212_SQL_PORT",
        "app_backup_dir": "/mnt/p6-backups/p62212",
        "sharepoint_folder": "P6 Backups/P62212",
        "data_file_path": "/var/database/Axial_DAT.MDF",
        "log_file_path": "/var/database/Axial_LOG.LDF",
    },
]


class Command(BaseCommand):
    help = "Create or update the default P6 MSSQL backup targets."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help="Update existing target connection/path defaults as well as creating missing targets.",
        )

    def handle(self, *args, **options):
        update_existing = bool(options["update_existing"])
        created = 0
        updated = 0

        for payload in DEFAULT_TARGETS:
            sql_port = int(getattr(settings, payload["sql_port_setting"]))
            defaults = {
                "name": payload["name"],
                "description": payload["description"],
                "sql_host": settings.P6_DEFAULT_SQL_HOST,
                "sql_port": sql_port,
                "sql_database": payload["sql_database"],
                "sql_username": "sa",
                "sql_password_env": payload["sql_password"],
                "sql_backup_dir": "/var/backups",
                "app_backup_dir": payload["app_backup_dir"],
                "sql_data_dir": "/var/database",
                "data_file_path": payload["data_file_path"],
                "log_file_path": payload["log_file_path"],
                "sharepoint_folder": payload["sharepoint_folder"],
            }
            target, was_created = BackupTarget.objects.get_or_create(
                slug=payload["slug"],
                defaults=defaults,
            )
            if was_created:
                created += 1
                continue
            if update_existing:
                for key, value in defaults.items():
                    if key == "sql_password_env" and not value:
                        continue
                    setattr(target, key, value)
                update_fields = [key for key, value in defaults.items() if key != "sql_password_env" or value]
                target.save(update_fields=[*update_fields, "updated_at"])
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Targets created={created} updated={updated}"))
