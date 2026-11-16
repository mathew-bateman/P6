"""One capacity report for the P6 page and the fleet monitoring probe."""
import math

from django.utils import timezone

from backups.models import BackupTarget
from backups.services.database_capacity import CRITICAL_PERCENT, WARNING_PERCENT, collect_database_capacity
from backups.services.postgres_capacity import collect_postgres_capacity


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _threshold(value):
    return "red" if value >= CRITICAL_PERCENT else "amber" if value >= WARNING_PERCENT else "green"


def _sql_row(row):
    target = row["target"]
    edition = str(row.get("edition", ""))[:128]
    limit = _number(row.get("limit_gb"))
    result = {
        "engine": "sqlserver", "name": " ".join(str(target.name).split())[:120], "slug": str(target.slug),
        "database_name": str(getattr(target, "sql_database", ""))[:128],
        "backups_enabled": bool(getattr(target, "enabled", True)),
        "target_url": f"/targets/{target.slug}/", "edition": edition,
        "version": str(row.get("version", ""))[:128], "status": row.get("status", "unavailable"),
        "limit_status": "known" if limit else "unavailable" if not edition or "express" in edition.lower() or "unknown" in edition.lower() else "not_applicable",
        "used_gb": _number(row.get("data_used_gb")), "allocated_gb": _number(row.get("data_allocated_gb")),
        "limit_gb": limit, "remaining_gb": _number(row.get("remaining_gb")),
        "used_percent": _number(row.get("used_percent")), "log_status": row.get("log_status", "unavailable"),
        "capacity_note": str(row.get("capacity_note", ""))[:300],
        "checked_at": row["checked_at"].isoformat() if hasattr(row.get("checked_at"), "isoformat") else str(row.get("checked_at") or timezone.now().isoformat()),
    }
    result.update({key: _number(row.get(key)) for key in ("log_used_gb", "log_used_percent", "log_allocated_gb", "log_total_gb")})
    return result


def row_level(row):
    if row.get("status") != "ok" or row.get("used_gb") is None:
        return "red"
    if row.get("engine") == "postgresql":
        if row.get("storage_status") != "ok" or row.get("storage_used_percent") is None:
            return "red"
        return _threshold(row["storage_used_percent"])
    if row.get("limit_status") == "unavailable" or row.get("log_status") != "ok" or row.get("log_used_percent") is None:
        return "red"
    if row.get("limit_status") == "known" and row.get("used_percent") is None:
        return "red"
    return _threshold(max(row.get("used_percent") or 0, row["log_used_percent"]))


def row_summary(row):
    if row["engine"] == "postgresql":
        size = f"database {row['used_gb']:.2f} GB" if row["status"] == "ok" and row["used_gb"] is not None else "database usage unavailable"
        storage = f"shared disk {row['storage_used_percent']:.1f}% ({row['storage_available_gb']:.2f} GB available)" if row.get("storage_status") == "ok" else "shared disk usage unavailable"
        return f"{row['name']}: {size}, {storage}"
    readings = []
    if row["status"] != "ok" or row["used_gb"] is None:
        readings.append("data usage unavailable")
    elif row["limit_status"] == "known" and row["used_percent"] is not None:
        readings.append(f"data {row['used_percent']:.1f}% ({row['used_gb']:.2f}/{row['limit_gb']:g} GB)")
    elif row["limit_status"] == "unavailable":
        readings.append("data limit unconfirmed")
    else:
        readings.append(f"data {row['used_gb']:.2f} GB")
    readings.append(f"log {row['log_used_percent']:.1f}%" if row["log_status"] == "ok" and row["log_used_percent"] is not None else "log usage unavailable")
    return f"{row['name']}: " + ", ".join(readings)


def build_capacity_report(rows):
    databases = []
    for row in rows:
        if row.get("engine") == "postgresql":
            result = dict(row)
            result.update(limit_status="not_applicable", limit_gb=None, used_percent=None,
                          allocated_gb=None, remaining_gb=None, log_status="not_applicable",
                          log_used_gb=None, log_used_percent=None, log_allocated_gb=None, log_total_gb=None)
        else:
            result = _sql_row(row)
        result["level"] = row_level(result)
        databases.append(result)
    ranks = {"green": 0, "amber": 1, "red": 2}
    level = max((row["level"] for row in databases), key=ranks.__getitem__, default="red")
    ordered = sorted(databases, key=lambda row: ranks[row["level"]], reverse=True)
    reason = "; ".join(row_summary(row) for row in ordered)[:500] if databases else "No database targets are configured; capacity could not be checked."
    return {"schema_version": 2, "checked_at": timezone.now().isoformat(), "level": level, "reason": reason, "databases": databases}


def collect_capacity_report():
    rows = collect_database_capacity(BackupTarget.objects.order_by("name", "pk"), include_log_usage=True)
    postgres = collect_postgres_capacity()
    if postgres is not None:
        rows.append(postgres)
    return build_capacity_report(rows)


def capacity_card_rows(report):
    cards = []
    for row in report["databases"]:
        card = dict(row)
        card["severity"] = {"green": "healthy", "amber": "warning", "red": "critical"}[row["level"]]
        used = min(100, max(0, row.get("used_percent") or 0))
        allocated = min(100, 100 * row["allocated_gb"] / row["limit_gb"]) if row.get("allocated_gb") is not None and row.get("limit_gb") else 0
        card.update(used_bar_percent=used, reserved_bar_percent=max(0, allocated - used))
        cards.append(card)
    return cards
