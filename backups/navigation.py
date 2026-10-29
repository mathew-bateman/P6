"""Permission-aware presentation metadata for the shared P6 workspace shell.

This module describes links only. Existing view permission checks remain the
authority for access, and no report-source or session state is changed here.
"""
from __future__ import annotations

from urllib.parse import urlencode

from django.apps import apps
from django.urls import NoReverseMatch, reverse


REPORT_SOURCES = frozenset({"combined", "p6_22", "p6_23"})
REPORT_FILTER_KEYS = (
    "portfolio", "project", "lead_planner", "project_status", "project_state",
    "discipline", "project_phase", "updated_date", "exclude_blanks", "check",
)


def section_for_route(url_name: str | None) -> str:
    name = url_name or ""
    if name == "suite_landing":
        return "home"
    if name.startswith("portfolio_reporting"):
        return "portfolio"
    if name in {"schedule_quality_overview", "schedule_quality_validation"}:
        return "quality"
    if name.startswith("schedule_quality_settings") or name == "schedule_quality_p6_fields_api":
        return "settings"
    if name.startswith("reporting_mirror"):
        return "reporting"
    if (
        name == "backup_dashboard" or name.startswith("backup_target_")
        or "maintenance" in name or "userdata" in name
        or name in {"schedule_quality_dashboard", "schedule_quality_refresh", "schedule_quality_schedule_update"}
    ):
        return "operations"
    return ""


def selected_report_source(request) -> str:
    session = getattr(request, "session", {})
    candidates = (
        request.GET.get("report_source"),
        getattr(request, "p6_report_source", None),
        session.get("p6_report_source"),
        session.get("report_source"),
    )
    return next((value for value in candidates if isinstance(value, str) and value in REPORT_SOURCES), "")


def report_query(request, *, preserve_filters: bool = False) -> str:
    values = []
    source = selected_report_source(request)
    if source:
        values.append(("report_source", source))
    if preserve_filters:
        for key in REPORT_FILTER_KEYS:
            for value in request.GET.getlist(key)[:16]:
                values.append((key, value[:200]))
    return "?" + urlencode(values) if values else ""


def _reverse_optional(name: str) -> str:
    try:
        return reverse(name)
    except NoReverseMatch:
        return ""


def build_workspace_navigation(request, *, can_view_quality=False, can_view_portfolio=False, can_edit_settings=False):
    user = request.user
    installed_mirror = apps.is_installed("reporting_mirror")
    mirror_url = _reverse_optional("reporting_mirror_dashboard") if installed_mirror else ""
    mirror_allowed = bool(mirror_url and (user.is_staff or can_view_quality or can_view_portfolio))
    active_id = section_for_route(getattr(getattr(request, "resolver_match", None), "url_name", None))
    reset_query = report_query(request)
    definitions = [{"id": "home", "label": "Home", "icon": "house", "route": "suite_landing", "groups": []}]
    if can_view_portfolio:
        definitions.append({"id": "portfolio", "label": "Portfolio Reporting", "icon": "chart-bar", "route": "portfolio_reporting_overview", "groups": [
            {"id": "portfolio-pages", "label": "Portfolio Reporting", "links": [
                ("portfolio_reporting_overview", "Overview"),
                ("portfolio_reporting_milestones", "Milestones"),
                ("portfolio_reporting_risk_float", "Risk & float"),
                ("portfolio_reporting_health", "Schedule health"),
                ("portfolio_reporting_project_detail", "Projects"),
                ("portfolio_reporting_resources", "Resources"),
            ]},
        ]})
    if can_view_quality:
        definitions.append({"id": "quality", "label": "Quality Reports", "icon": "shield-check", "route": "schedule_quality_overview", "groups": [
            {"id": "quality-pages", "label": "Quality Reports", "links": [
                ("schedule_quality_overview", "Programme check"),
                ("schedule_quality_validation", "Validation & evidence"),
            ]},
        ]})
    operations = []
    if user.is_staff:
        operations.extend([
            {"id": "backups", "label": "Backup Targets", "links": [("backup_dashboard", "Targets & schedules")]},
            {"id": "maintenance", "label": "Database Maintenance", "links": [("maintenance_dashboard", "Maintenance history")]},
        ])
    operations.append({"id": "refresh", "label": "Schedule Quality", "links": [("schedule_quality_dashboard", "Data refresh")]})
    definitions.append({"id": "operations", "label": "Operations", "icon": "database", "route": "backup_dashboard" if user.is_staff else "schedule_quality_dashboard", "groups": operations})
    if mirror_allowed:
        definitions.append({"id": "reporting", "label": "Reporting Database", "icon": "database", "route": "reporting_mirror_dashboard", "groups": [
            {"id": "reporting-database", "label": "Reporting Database", "links": [("reporting_mirror_dashboard", "Reporting database")]},
        ]})
    if can_edit_settings:
        definitions.append({"id": "settings", "label": "Quality Settings", "icon": "gear-six", "route": "schedule_quality_settings", "groups": [
            {"id": "quality-settings", "label": "Quality Settings", "links": [("schedule_quality_settings", "Quality rules")]},
        ]})

    route_name = getattr(getattr(request, "resolver_match", None), "url_name", "")
    sections = []
    for definition in definitions:
        destination = _reverse_optional(definition["route"])
        if not destination:
            continue
        section = {**definition, "url": destination + reset_query, "active": definition["id"] == active_id, "icon_path": f"backups/icons/{definition['icon']}.svg", "groups": []}
        preserve = definition["id"] == active_id and active_id in {"portfolio", "quality"}
        for group in definition["groups"]:
            links = []
            for name, label in group["links"]:
                destination = _reverse_optional(name)
                if destination:
                    active = (
                        name == route_name
                        or (name == "backup_dashboard" and route_name.startswith("backup_target_"))
                        or (name == "maintenance_dashboard" and ("maintenance" in route_name or "userdata" in route_name))
                        or (name == "reporting_mirror_dashboard" and active_id == "reporting")
                    )
                    links.append({"id": name, "label": label, "url": destination + report_query(request, preserve_filters=preserve), "reset_url": destination + reset_query, "active": active})
            if links:
                section["groups"].append({"id": group["id"], "label": group["label"], "links": links})
        sections.append(section)
    active_section = next((section for section in sections if section["active"]), None)
    name = user.get_full_name().strip() or user.get_username()
    parts = name.replace(".", " ").replace("_", " ").split()
    initials = "".join(part[0] for part in (parts[:1] + parts[-1:] if len(parts) > 1 else parts))[:2].upper() or "P6"
    return {
        "sections": sections,
        "active_section": active_section,
        "is_home": active_id == "home",
        "has_sidebar": bool(active_section and active_section["groups"] and active_id != "home"),
        "user_label": name,
        "initials": initials,
        "mirror_installed": installed_mirror,
        "mirror_available": mirror_allowed,
    }
