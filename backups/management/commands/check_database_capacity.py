"""Emit the fixed, read-only capacity probe consumed by fleet monitoring."""
from __future__ import annotations

import json
import logging
import math

from django.core.management.base import BaseCommand

from backups.models import BackupTarget
from backups.services.database_capacity import (
    CRITICAL_PERCENT,
    WARNING_PERCENT,
    collect_database_capacity,
)


logger = logging.getLogger(__name__)


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def build_capacity_report(rows) -> dict:
    """Summarise data-limit and log-fill readings without exposing connections."""
    databases, summaries = [], []
    level = "green"
    ranks = {"green": 0, "amber": 1, "red": 2}
    for row in rows:
        target = row["target"]
        name = " ".join(str(target.name).split())[:120]
        edition = str(row.get("edition", "")).lower()
        limit_status = "known" if row.get("limit_gb") is not None else "unavailable" if not edition or "express" in edition or "unknown" in edition else "not_applicable"
        database = {
            "name": name,
            "slug": target.slug,
            "used_gb": _number(row.get("data_used_gb")),
            "limit_gb": _number(row.get("limit_gb")),
            "limit_status": limit_status,
            "used_percent": _number(row.get("used_percent")),
            "log_used_percent": _number(row.get("log_used_percent")),
            "log_allocated_gb": _number(row.get("log_allocated_gb")),
            "log_total_gb": _number(row.get("log_total_gb")),
            "log_used_gb": _number(row.get("log_used_gb")),
            "status": row.get("status", "unavailable"),
            "log_status": row.get("log_status", "unavailable"),
        }
        databases.append(database)
        issues = []
        row_level = "green"
        if database["status"] != "ok" or database["used_gb"] is None:
            issues.append("data usage unavailable")
            row_level = "red"
        elif database["limit_gb"] is None:
            edition = str(row.get("edition", "")).lower()
            if not edition or "express" in edition or "unknown" in edition:
                issues.append("data limit unconfirmed")
                row_level = "red"
        elif database["used_percent"] is None:
            issues.append("data percentage unavailable")
            row_level = "red"
        if database["log_status"] != "ok" or database["log_used_percent"] is None:
            issues.append("log usage unavailable")
            row_level = "red"
        for value in (database["used_percent"], database["log_used_percent"]):
            if value is not None and value >= CRITICAL_PERCENT:
                row_level = "red"
            elif value is not None and value >= WARNING_PERCENT and row_level == "green":
                row_level = "amber"
        if ranks[row_level] > ranks[level]:
            level = row_level
        readings = []
        if database["status"] == "ok" and database["used_gb"] is not None:
            if database["limit_gb"] is not None and database["used_percent"] is not None:
                readings.append(f"data {database['used_percent']:.1f}% ({database['used_gb']:.2f}/{database['limit_gb']:g} GB)")
            else:
                readings.append(f"data {database['used_gb']:.2f} GB")
        if database["log_status"] == "ok" and database["log_used_percent"] is not None:
            readings.append(f"log {database['log_used_percent']:.1f}%")
        summaries.append((ranks[row_level], f"{name}: " + ", ".join(issues + readings)))
    if not databases:
        level, reason = "red", "No database targets are configured; capacity could not be checked."
    else:
        # Put failures and thresholds first so truncation retains the action needed.
        summaries.sort(key=lambda summary: summary[0], reverse=True)
        reason = "; ".join(summary[1] for summary in summaries)[:500]
    return {"schema_version": 1, "level": level, "reason": reason, "databases": databases}


class Command(BaseCommand):
    help = "Read data and transaction log usage for all configured databases and emit a JSON monitoring result."
    requires_system_checks = []

    def handle(self, *args, **options):
        try:
            rows = collect_database_capacity(
                BackupTarget.objects.all().order_by("name", "pk"),
                include_log_usage=True,
            )
            report = build_capacity_report(rows)
        except Exception:
            logger.warning("The database capacity monitoring probe could not complete.")
            report = {
                "schema_version": 1,
                "level": "red",
                "reason": "Database capacity collection failed. Check the service and target connections.",
                "databases": [],
            }
        self.stdout.write(json.dumps(report, ensure_ascii=True, allow_nan=False))
