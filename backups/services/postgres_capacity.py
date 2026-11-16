"""Read-only reporting PostgreSQL usage and its explicitly configured volume.

The reporting mirror is an optional production module. Import its reader only
when enabled so a standard checkout remains usable without PostgreSQL packages.
"""
from __future__ import annotations

from importlib import import_module
import logging
import os
from collections.abc import Mapping
from datetime import timezone as datetime_timezone

from django.apps import apps
from django.conf import settings
from django.utils import timezone


logger = logging.getLogger(__name__)
BYTES_PER_GB = 1024 ** 3

POSTGRES_CAPACITY_SQL = """
WITH relation_sizes AS (
    SELECT
        COALESCE(SUM(pg_table_size(relid)), 0) AS tables_bytes,
        COALESCE(SUM(pg_indexes_size(relid)), 0) AS indexes_bytes
    FROM pg_statio_user_tables
)
SELECT
    current_database() AS database_name,
    current_setting('server_version') AS version,
    pg_database_size(current_database()) AS database_bytes,
    tables_bytes,
    indexes_bytes
FROM relation_sizes;
"""


def reporting_capacity_enabled() -> bool:
    explicit = getattr(settings, "P6_REPORTING_CAPACITY_ENABLED", None)
    if explicit is not None:
        return bool(explicit)
    return apps.is_installed("reporting_mirror")


def _postgres_usage() -> dict:
    unavailable = {
        "status": "unavailable", "database_name": "", "version": "",
        "used_gb": None, "tables_gb": None, "indexes_gb": None,
    }
    try:
        connections = import_module("reporting_mirror.connections")
        if not connections.reporting_available():
            return unavailable
        with connections.postgres_connection(reader=True) as connection:
            # The existing reader context defaults to a transaction and closes
            # the connection on exit. Both settings apply to this read only.
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '5s'")
            result = connection.execute(POSTGRES_CAPACITY_SQL).fetchone()
            if result is None:
                raise ValueError("PostgreSQL capacity statistics were not returned.")
            if isinstance(result, Mapping):
                database_name, version, database_bytes, tables_bytes, indexes_bytes = (
                    result[key]
                    for key in ("database_name", "version", "database_bytes", "tables_bytes", "indexes_bytes")
                )
            else:
                database_name, version, database_bytes, tables_bytes, indexes_bytes = result
            sizes = tuple(int(size) for size in (database_bytes, tables_bytes, indexes_bytes))
            if min(sizes) < 0 or sizes[1] + sizes[2] > sizes[0] or not str(database_name or "").strip():
                raise ValueError("PostgreSQL capacity statistics were invalid.")
            return {
                "status": "ok",
                "database_name": str(database_name),
                "version": str(version or "")[:120],
                "used_gb": sizes[0] / BYTES_PER_GB,
                "tables_gb": sizes[1] / BYTES_PER_GB,
                "indexes_gb": sizes[2] / BYTES_PER_GB,
            }
    except Exception:
        # Connection exceptions can include credentials; never interpolate them.
        logger.warning("PostgreSQL reporting database capacity could not be collected.")
        return unavailable


def _storage_usage() -> dict:
    unavailable = {
        "storage_status": "unavailable",
        "storage_total_gb": None,
        "storage_used_gb": None,
        "storage_available_gb": None,
        "storage_reserved_gb": None,
        "storage_usable_gb": None,
        "storage_used_percent": None,
    }
    storage_path = getattr(settings, "P6_REPORTING_CAPACITY_STORAGE_PATH", os.getenv("P6_REPORTING_CAPACITY_STORAGE_PATH", ""))
    if not storage_path or not str(storage_path).strip():
        return unavailable
    try:
        stat = os.statvfs(storage_path)
        blocks, free_blocks, available_blocks, fragment_bytes = (
            int(value) for value in (stat.f_blocks, stat.f_bfree, stat.f_bavail, stat.f_frsize)
        )
        if (
            blocks <= 0 or fragment_bytes <= 0
            or not 0 <= available_blocks <= free_blocks <= blocks
        ):
            raise ValueError("The configured storage volume counters were invalid.")
        used_bytes = (blocks - free_blocks) * fragment_bytes
        available_bytes = available_blocks * fragment_bytes
        usable_bytes = used_bytes + available_bytes
        if usable_bytes <= 0:
            raise ValueError("The configured storage volume has no usable capacity.")
        return {
            "storage_status": "ok",
            "storage_total_gb": blocks * fragment_bytes / BYTES_PER_GB,
            "storage_used_gb": used_bytes / BYTES_PER_GB,
            "storage_available_gb": available_bytes / BYTES_PER_GB,
            "storage_reserved_gb": (free_blocks - available_blocks) * fragment_bytes / BYTES_PER_GB,
            "storage_usable_gb": usable_bytes / BYTES_PER_GB,
            "storage_used_percent": used_bytes / usable_bytes * 100,
        }
    except Exception:
        logger.warning("PostgreSQL reporting storage capacity could not be collected.")
        return unavailable


def collect_postgres_capacity() -> dict | None:
    """Return database and configured filesystem readings independently.

    No database edition cap is assumed. The filesystem describes the whole
    PostgreSQL host volume, including its other occupants and reserved blocks;
    it is not a dedicated quota for this one database. WAL is not inspected.
    """
    if not reporting_capacity_enabled():
        return None
    return {
        "engine": "postgresql",
        "name": "PostgreSQL reporting",
        "slug": "postgresql-reporting",
        **_postgres_usage(),
        **_storage_usage(),
        "checked_at": timezone.now().astimezone(datetime_timezone.utc).isoformat(),
    }
