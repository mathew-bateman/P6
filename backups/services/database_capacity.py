"""Read-only SQL Server database capacity, separate from backup file sizes."""
from __future__ import annotations

import logging
import math

import pyodbc
from django.utils import timezone

from backups.services.mssql import build_connection_string


logger = logging.getLogger(__name__)
WARNING_PERCENT = 80
CRITICAL_PERCENT = 90
BYTES_PER_GB = 1024 ** 3
PAGES_PER_GB = 131072  # SQL Server pages are 8 KiB; GB follows SQL Server's 1024 MB convention.
UNAVAILABLE_MESSAGE = "Usage could not be read. Check the target connection and SQL permissions, then refresh."

# Aggregate every ROWS file. Transaction logs never consume the Express data limit.
# Explicit casts avoid pyodbc's unsupported sql_variant result type.
CAPACITY_SQL = """
SELECT
    CAST(SERVERPROPERTY('Edition') AS nvarchar(128)) AS edition,
    CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version,
    CAST(SERVERPROPERTY('ProductMajorVersion') AS nvarchar(16)) AS major_version,
    SUM(CASE WHEN type = 0 THEN CONVERT(bigint, size) ELSE 0 END) AS data_allocated_pages,
    SUM(CASE WHEN type = 0 THEN CONVERT(bigint, FILEPROPERTY(name, 'SpaceUsed')) ELSE 0 END) AS data_used_pages,
    SUM(CASE WHEN type = 1 THEN CONVERT(bigint, size) ELSE 0 END) AS log_allocated_pages,
    SUM(CASE WHEN type = 0 AND FILEPROPERTY(name, 'SpaceUsed') IS NULL THEN 1 ELSE 0 END) AS unreadable_data_files,
    SUM(CASE WHEN type = 0 THEN 1 ELSE 0 END) AS data_file_count
FROM sys.database_files;
"""


def _edition_limit(edition: str, major_version: object) -> int | None:
    if "express" not in edition.lower():
        return None
    try:
        major = int(major_version)
    except (TypeError, ValueError):
        return None
    if major <= 0:
        return None
    return 50 if major >= 17 else 10


LOG_USAGE_SQL = """
SELECT total_log_size_in_bytes, used_log_space_in_bytes, used_log_space_in_percent
FROM sys.dm_db_log_space_usage;
"""


def _log_defaults(*, checked: bool) -> dict:
    return {
        "log_status": "unavailable" if checked else "not_checked",
        "log_used_gb": None,
        "log_used_percent": None,
        "log_remaining_gb": None,
        "log_total_gb": None,
    }


def _read_log_usage(connection) -> dict:
    """A permission failure here must not hide successful data-file statistics."""
    try:
        cursor = connection.cursor()
        cursor.execute(LOG_USAGE_SQL)
        row = cursor.fetchone()
        if not row:
            raise ValueError("Log space statistics were not returned.")
        total_bytes, used_bytes = int(row[0]), int(row[1])
        used_percent = float(row[2])
        if (
            total_bytes <= 0 or used_bytes < 0 or used_bytes > total_bytes
            or not math.isfinite(used_percent) or not 0 <= used_percent <= 100
        ):
            raise ValueError("Log space statistics were inconsistent.")
        return {
            "log_status": "ok",
            "log_used_gb": used_bytes / BYTES_PER_GB,
            "log_used_percent": used_percent,
            "log_remaining_gb": (total_bytes - used_bytes) / BYTES_PER_GB,
            "log_total_gb": total_bytes / BYTES_PER_GB,
        }
    except Exception:
        logger.warning("Database transaction log usage could not be collected for a configured target.")
        return _log_defaults(checked=True)


def _read_capacity(target, *, include_log_usage: bool = False) -> dict:
    connection = None
    try:
        connection = pyodbc.connect(
            build_connection_string(target, database=target.sql_database),
            autocommit=True,
            timeout=5,
        )
        connection.timeout = 10
        cursor = connection.cursor()
        cursor.execute(CAPACITY_SQL)
        row = cursor.fetchone()
        if not row or row[6] or not row[7]:
            raise ValueError("Database data-file usage was not returned in full.")
        allocated_pages, used_pages, log_pages = (int(row[i]) for i in (3, 4, 5))
        if min(allocated_pages, used_pages, log_pages) < 0 or used_pages > allocated_pages:
            raise ValueError("Database capacity counters were inconsistent.")
        edition, version = str(row[0] or "Unknown edition"), str(row[1] or "Unknown version")
        limit_gb = _edition_limit(edition, row[2])
        data_used_gb = used_pages / PAGES_PER_GB
        data_allocated_gb = allocated_pages / PAGES_PER_GB
        log_allocated_gb = log_pages / PAGES_PER_GB
        used_percent = 100 * data_used_gb / limit_gb if limit_gb else None
        allocated_percent = 100 * data_allocated_gb / limit_gb if limit_gb else None
        used_bar_percent = min(100.0, used_percent) if used_percent is not None else 0.0
        allocated_bar_percent = min(100.0, allocated_percent) if allocated_percent is not None else 0.0
        severity = "critical" if used_percent is not None and used_percent >= CRITICAL_PERCENT else "warning" if used_percent is not None and used_percent >= WARNING_PERCENT else "healthy"
        if limit_gb:
            capacity_note = f"SQL Server Express data limit: {limit_gb} GB. Transaction logs are shown separately."
        elif "express" in edition.lower():
            capacity_note = "Express limit could not be confirmed because the SQL Server version is unavailable."
        elif edition == "Unknown edition":
            capacity_note = "The database limit could not be confirmed because the SQL Server edition is unavailable."
        else:
            capacity_note = "This edition does not have the SQL Server Express database size limit."
        return {
            "status": "ok",
            "edition": edition,
            "version": version,
            "limit_gb": limit_gb,
            "data_used_gb": data_used_gb,
            "data_allocated_gb": data_allocated_gb,
            "log_allocated_gb": log_allocated_gb,
            "remaining_gb": max(0.0, limit_gb - data_used_gb) if limit_gb else None,
            "used_percent": used_percent,
            "allocated_percent": allocated_percent,
            "used_bar_percent": used_bar_percent,
            "reserved_bar_percent": max(0.0, allocated_bar_percent - used_bar_percent),
            "severity": severity,
            "checked_at": timezone.now(),
            "unavailable_message": "",
            "capacity_note": capacity_note,
            **(_read_log_usage(connection) if include_log_usage else _log_defaults(checked=False)),
        }
    except Exception:
        # ODBC errors can contain connection strings or other credential material.
        # Neither user-visible errors nor logging should interpolate the exception.
        logger.warning("Database capacity collection failed for a configured target.")
        return {
            "status": "unavailable",
            "edition": "",
            "version": "",
            "limit_gb": None,
            "data_used_gb": None,
            "data_allocated_gb": None,
            "log_allocated_gb": None,
            "remaining_gb": None,
            "used_percent": None,
            "allocated_percent": None,
            "used_bar_percent": 0.0,
            "reserved_bar_percent": 0.0,
            "severity": "unavailable",
            "checked_at": timezone.now(),
            "unavailable_message": UNAVAILABLE_MESSAGE,
            "capacity_note": "",
            **_log_defaults(checked=include_log_usage),
        }
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.warning("Database capacity connection could not be closed cleanly.")


def collect_database_capacity(targets, *, include_log_usage: bool = False) -> list[dict]:
    """Return one row per configured target, including disabled backup targets.

    Repeated targets with exactly the same connection configuration share a read
    during this collection. Different SQL credentials must never share results.
    """
    collected = {}
    rows = []
    for target in targets:
        key = (
            target.sql_host.casefold(), target.sql_port, target.sql_database,
            target.sql_username, target.sql_password, target.odbc_driver,
            target.encrypt_connection, target.trust_server_certificate,
        )
        if key not in collected:
            collected[key] = _read_capacity(target, include_log_usage=include_log_usage)
        rows.append({**collected[key], "target": target})
    return rows
