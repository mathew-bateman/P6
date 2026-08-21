from __future__ import annotations

import logging
import re
import secrets
from dataclasses import replace
from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.mixins import UserPassesTestMixin
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.core.paginator import Paginator
from django.db.models import Sum
from django.urls import reverse
from django.views import View
from django.views.generic import ListView, TemplateView

from backups.forms import ScheduleQualitySettingsForm
from backups.models import BackupRun, BackupTarget, DatabaseMaintenanceRun
from backups.services.schedule_quality import (
    ScheduleQualitySettingsConflict,
    count_schedule_quality_refresh_history,
    fetch_schedule_quality_settings,
    fetch_schedule_quality_refresh_history,
    get_or_create_schedule_quality_draft,
    get_schedule_quality_profile_code,
    save_schedule_quality_draft,
)
from backups.services.schedule_quality_reporting import (
    ScheduleQualityValidationFilters,
    fetch_programme_overview,
    fetch_validation_evidence,
    fetch_validation_filter_options,
    fetch_validation_summary,
)
from backups.services.portfolio_reporting import (
    fetch_milestone_governance,
    fetch_portfolio_overview,
    fetch_project_detail,
    fetch_resource_overview,
    fetch_schedule_health,
    fetch_schedule_risk_and_float,
)
from backups.services.restore import build_sharepoint_backup_rows
from backups.services.scheduling import (
    get_schedule_quality_refresh_schedule,
    sync_schedule_quality_refresh_schedule,
    sync_target_schedule,
)
from backups.services.sharepoint import build_sharepoint_client, parse_sharepoint_url
from backups.tasks import (
    execute_schedule_quality_refresh,
    execute_target_backup,
    execute_target_restore,
    publish_schedule_quality_config_task,
)


logger = logging.getLogger(__name__)

SCHEDULE_QUALITY_HISTORY_PAGE_SIZE = 10
SCHEDULE_QUALITY_EVIDENCE_PAGE_SIZE = 25
TABLE_PAGE_SIZE = 10
USERDATA_FREED_CAPACITY_PATTERN = re.compile(
    r"(?:total\s+)?(?:space|capacity)\s+freed\s*:\s*([\d.]+)\s*(GB|MB)",
    re.IGNORECASE,
)
RESTORE_CONFIRMATION_WORDS = (
    "amber", "birch", "cobalt", "dawn", "ember", "forest", "harbour",
    "indigo", "juniper", "maple", "meadow", "orbit", "quartz", "river",
    "saffron", "summit", "violet", "willow",
)


def restore_confirmation_session_key(target: BackupTarget) -> str:
    return f"restore_confirmation_word:{target.slug}"


def parse_non_negative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def build_schedule_quality_change_note(snapshot, payload: dict[str, list[dict[str, object]]]) -> str:
    """Build a concise, server-owned before/after audit note for a publish."""

    def display(value: object) -> str:
        if value is None:
            return "N/A"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return str(value)

    changes: list[str] = []
    scope_fields = (
        ("is_enabled", "Enabled"),
        ("include_loe", "Include LOE"),
        ("include_wbs_summary", "Include WBS summary"),
        ("include_milestones", "Include milestones"),
        ("exclude_complete", "Exclude complete"),
        ("limit_type", "Limit type"),
        ("green_limit", "Green limit"),
        ("amber_limit", "Amber limit"),
        ("green_points", "Green points"),
        ("amber_points", "Amber points"),
    )
    previous_checks = {check.check_code: check for check in snapshot.checks}
    for proposed in payload["checks"]:
        previous = previous_checks[str(proposed["check_code"])]
        before_after = [
            f"{label}: {display(getattr(previous, name))} to {display(proposed[name])}"
            for name, label in scope_fields
            if getattr(previous, name) != proposed[name]
        ]
        if before_after:
            changes.append(f"{previous.display_name} — " + "; ".join(before_after))

    for proposed in payload["options"]:
        previous = next(option for option in snapshot.options if option.option_code == proposed["option_code"])
        before = previous.bit_value if previous.data_type == "bit" else previous.numeric_value if previous.data_type in {"integer", "decimal"} else previous.text_value
        after = proposed["bit_value"] if previous.data_type == "bit" else proposed["numeric_value"] if previous.data_type in {"integer", "decimal"} else proposed["text_value"]
        if before != after:
            changes.append(f"{previous.display_name}: {display(before)} to {display(after)}")

    previous_constraints = {constraint.constraint_type_code: constraint for constraint in snapshot.constraint_types}
    for proposed in payload["constraint_types"]:
        previous = previous_constraints[str(proposed["constraint_type_code"])]
        if previous.is_checked != proposed["is_checked"]:
            changes.append(
                f"{previous.display_name}: {display(previous.is_checked)} to {display(proposed['is_checked'])}"
            )

    def evidence_summary(fields: list[dict[str, object]]) -> str:
        return ", ".join(
            f"{field['source_category']}.{field['source_identifier']} ({field['display_label']})"
            for field in sorted(fields, key=lambda field: int(field["sort_order"]))
        ) or "none"

    check_names = {check.check_code: check.display_name for check in snapshot.checks}
    previous_evidence = [
        {
            "check_code": field.check_code,
            "source_category": field.source_category,
            "source_identifier": field.source_identifier,
            "display_label": field.display_label,
            "sort_order": field.sort_order,
        }
        for field in snapshot.detail_fields
    ]
    for check_code in check_names:
        before = [field for field in previous_evidence if field["check_code"] == check_code]
        after = [field for field in payload["detail_fields"] if field["check_code"] == check_code]
        if before != after:
            changes.append(
                f"Evidence — {check_names[check_code]}: {evidence_summary(before)} to {evidence_summary(after)}"
            )

    note = "Published changes: " + ("; ".join(changes) if changes else "no configuration changes.")
    return note if len(note) <= 500 else note[:497].rstrip() + "..."


class StaffRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_staff


def can_view_schedule_quality_reports(user) -> bool:
    return bool(
        user.is_authenticated
        and user.groups.filter(
            name=settings.P6_SCHEDULE_QUALITY_REPORT_GROUP
        ).exists()
    )


class ScheduleQualityReportRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return can_view_schedule_quality_reports(self.request.user)


def can_edit_schedule_quality_schedule(user) -> bool:
    return bool(
        user.is_authenticated
        and (
            user.is_staff
            or user.groups.filter(
                name=settings.P6_SCHEDULE_QUALITY_EDITOR_GROUP
            ).exists()
        )
    )


def can_edit_schedule_quality_settings(user) -> bool:
    return can_edit_schedule_quality_schedule(user)


def schedule_quality_actor(user) -> str:
    return str(user.get_username() or f"user-{user.pk}")[:150]


def parse_backup_schedule_for_form(crontab_string: str) -> dict[str, str | list[str]]:
    default_payload: dict[str, str | list[str]] = {
        "schedule_type": "weekly",
        "backup_time": "02:00",
        "backup_days": ["0"],
    }
    parts = (crontab_string or "").strip().split()
    if len(parts) != 5:
        return default_payload

    minute, hour, day_of_month, month, day_of_week = parts
    if not (minute.isdigit() and hour.isdigit()):
        default_payload["schedule_type"] = "custom"
        return default_payload
    minute_int = int(minute)
    hour_int = int(hour)
    if minute_int > 59 or hour_int > 23:
        default_payload["schedule_type"] = "custom"
        return default_payload

    default_payload["backup_time"] = f"{hour_int:02d}:{minute_int:02d}"
    if day_of_month != "*" or month != "*":
        default_payload["schedule_type"] = "custom"
        return default_payload
    if day_of_week == "*":
        default_payload["schedule_type"] = "daily"
        default_payload["backup_days"] = [str(index) for index in range(7)]
        return default_payload

    selected_days: list[str] = []
    for item in day_of_week.split(","):
        token = item.strip()
        if not token.isdigit():
            default_payload["schedule_type"] = "custom"
            return default_payload
        day_num = int(token)
        if day_num < 0 or day_num > 6:
            default_payload["schedule_type"] = "custom"
            return default_payload
        selected_days.append(str(day_num))

    default_payload["backup_days"] = sorted(set(selected_days), key=int) or ["0"]
    default_payload["schedule_type"] = "weekly"
    return default_payload


def describe_schedule_for_display(schedule_payload: dict[str, str | list[str]]) -> str:
    schedule_type = str(schedule_payload.get("schedule_type", "custom"))
    backup_time = str(schedule_payload.get("backup_time", "02:00"))
    backup_days = schedule_payload.get("backup_days", [])
    day_names = {
        "1": "Mon",
        "2": "Tue",
        "3": "Wed",
        "4": "Thu",
        "5": "Fri",
        "6": "Sat",
        "0": "Sun",
    }

    if schedule_type == "daily":
        return f"Daily at {backup_time}"
    if schedule_type == "weekly" and isinstance(backup_days, list):
        days = ", ".join(day_names.get(str(day), str(day)) for day in backup_days)
        return f"Runs {days} at {backup_time}"
    return "Uses a custom schedule"


def build_schedule_from_post(request) -> str | None:
    schedule_type = request.POST.get("schedule_type", "weekly")
    backup_schedule = request.POST.get("backup_schedule", "0 2 * * 0")
    backup_time = request.POST.get("backup_time", "02:00").strip()
    backup_days = request.POST.getlist("backup_days")

    if schedule_type not in {"daily", "weekly"}:
        return backup_schedule
    if ":" not in backup_time:
        return None
    hour_part, minute_part = backup_time.split(":", 1)
    if not (hour_part.isdigit() and minute_part.isdigit()):
        return None
    hour = int(hour_part)
    minute = int(minute_part)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    if schedule_type == "daily":
        day_of_week = "*"
    else:
        valid_days = sorted(
            {day for day in backup_days if day.isdigit() and 0 <= int(day) <= 6},
            key=int,
        )
        if not valid_days:
            return None
        day_of_week = ",".join(valid_days)
    return f"{minute} {hour} * * {day_of_week}"


class LandingPageView(LoginRequiredMixin, TemplateView):
    template_name = "backups/landing.html"


class DashboardView(StaffRequiredMixin, ListView):
    model = BackupTarget
    template_name = "backups/dashboard.html"
    context_object_name = "targets"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.is_staff:
            return redirect("schedule_quality_dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for target in context["targets"]:
            target.schedule_display = describe_schedule_for_display(
                parse_backup_schedule_for_form(target.backup_schedule)
            ).removeprefix("Runs ")
        recent_runs = BackupRun.objects.select_related("target").order_by("-started_at")
        selected_target = self.request.GET.get("target", "")
        if selected_target and BackupTarget.objects.filter(slug=selected_target).exists():
            recent_runs = recent_runs.filter(target__slug=selected_target)
        else:
            selected_target = ""
        page_obj = Paginator(recent_runs, TABLE_PAGE_SIZE).get_page(self.request.GET.get("page"))
        context["recent_runs"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["selected_target"] = selected_target
        return context


class ScheduleQualityDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "backups/schedule_quality.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_number = parse_non_negative_int(self.request.GET.get("page"), 1) or 1
        page_size = SCHEDULE_QUALITY_HISTORY_PAGE_SIZE
        selected_status = self.request.GET.get("status", "").strip().lower()
        if selected_status not in {"success", "failed", "running"}:
            selected_status = ""
        try:
            total_runs = count_schedule_quality_refresh_history(status=selected_status)
            total_pages = max(1, (total_runs + page_size - 1) // page_size)
            page_number = min(page_number, total_pages)
            context["refresh_history"] = fetch_schedule_quality_refresh_history(
                limit=page_size,
                offset=(page_number - 1) * page_size,
                status=selected_status,
            )
            context["history_page"] = {
                "number": page_number,
                "total_pages": total_pages,
                "page_range": range(1, total_pages + 1),
                "total_runs": total_runs,
                "has_previous": page_number > 1,
                "has_next": page_number < total_pages,
                "previous_page_number": page_number - 1,
                "next_page_number": page_number + 1,
                "start_index": ((page_number - 1) * page_size) + 1 if total_runs else 0,
                "end_index": min(page_number * page_size, total_runs),
            }
        except Exception as error:
            context["refresh_history"] = []
            context["refresh_history_error"] = str(error)
            context["history_page"] = {"number": 1, "total_pages": 1, "total_runs": 0}
        context["selected_status"] = selected_status

        schedule_state = get_schedule_quality_refresh_schedule(
            default_crontab=settings.P6_SCHEDULE_QUALITY_REFRESH_SCHEDULE,
            default_enabled=settings.P6_SCHEDULE_QUALITY_REFRESH_ENABLED,
        )
        context["schedule_quality_enabled"] = schedule_state["enabled"]
        context["schedule_quality_crontab"] = schedule_state["crontab"]
        context["can_edit_schedule_quality_schedule"] = can_edit_schedule_quality_schedule(
            self.request.user
        )
        context["can_edit_schedule_quality_settings"] = can_edit_schedule_quality_settings(
            self.request.user
        )
        schedule_payload = parse_backup_schedule_for_form(str(schedule_state["crontab"]))
        context.update(schedule_payload)
        context["schedule_quality_display"] = describe_schedule_for_display(schedule_payload)
        context["day_labels"] = [
            ("1", "Mon"),
            ("2", "Tue"),
            ("3", "Wed"),
            ("4", "Thu"),
            ("5", "Fri"),
            ("6", "Sat"),
            ("0", "Sun"),
        ]
        return context


class ScheduleQualityReportFiltersMixin:
    def _filters(self) -> ScheduleQualityValidationFilters:
        def project_filter_value(name: str, default: str) -> str:
            value = self.request.GET.get(name, default).strip()[:200]
            return "" if value == "__all__" else value

        raw_project_id = self.request.GET.get("project", "").strip()
        try:
            project_id = int(raw_project_id) if raw_project_id else None
            if project_id is not None and project_id < 1:
                project_id = None
        except ValueError:
            project_id = None

        raw_updated_date = self.request.GET.get("updated_date", "").strip()
        try:
            updated_date = date.fromisoformat(raw_updated_date) if raw_updated_date else None
        except ValueError:
            updated_date = None

        return ScheduleQualityValidationFilters(
            project_id=project_id,
            portfolio=self.request.GET.get("portfolio", "").strip()[:200],
            lead_planner=self.request.GET.get("lead_planner", "").strip()[:200],
            project_status=project_filter_value("project_status", "Client Submitted"),
            project_state=project_filter_value("project_state", "Live"),
            exclude_blanks=self.request.GET.get("exclude_blanks", "1") != "0",
            updated_date=updated_date,
            check_code=self.request.GET.get("check", "").strip()[:50],
        )

    def _filter_options_for_filters(
        self,
        filters: ScheduleQualityValidationFilters,
    ) -> tuple[ScheduleQualityValidationFilters, dict[str, object]]:
        options = fetch_validation_filter_options()
        projects = options["projects"]
        selected_project = next(
            (
                project
                for project in projects
                if project["proj_id"] == filters.project_id
            ),
            None,
        )
        project_portfolio = (
            str(selected_project["portfolio"])
            if selected_project and selected_project.get("portfolio")
            else ""
        )

        if filters.portfolio and project_portfolio and filters.portfolio != project_portfolio:
            filters = replace(filters, project_id=None)
        elif filters.project_id is not None and not filters.portfolio and project_portfolio:
            filters = replace(filters, portfolio=project_portfolio)

        scoped_projects = sorted(
            (
                project
                for project in projects
                if not filters.portfolio or project.get("portfolio") == filters.portfolio
            ),
            key=lambda project: (
                project.get("updated_date") is not None,
                project.get("updated_date") or date.min,
                project.get("proj_short_name") or "",
                project["proj_id"],
            ),
            reverse=True,
        )
        if filters.portfolio and filters.project_id is None and scoped_projects:
            # A portfolio represents successive programme submissions.  Its
            # default report must therefore be the newest linked submission,
            # rather than an aggregate of every historical version.
            latest_project = max(
                scoped_projects,
                key=lambda project: (
                    project.get("updated_date") is not None,
                    project.get("updated_date") or date.min,
                    project["proj_id"],
                ),
            )
            filters = replace(filters, project_id=int(latest_project["proj_id"]))
        return filters, {
            **options,
            "projects": scoped_projects,
        }


class ScheduleQualityOverviewView(
    ScheduleQualityReportRequiredMixin,
    ScheduleQualityReportFiltersMixin,
    TemplateView,
):
    template_name = "backups/schedule_quality_overview.html"

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            return ["backups/partials/schedule_quality_overview_results.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = self._filters()
        context["validation_filters"] = filters
        try:
            filters, options = self._filter_options_for_filters(filters)
            overview = fetch_programme_overview(filters)
            context["validation_filters"] = filters
            context.update(options)
            context.update(overview)
            selected_project = next(
                (
                    project
                    for project in options["projects"]
                    if project["proj_id"] == filters.project_id
                ),
                None,
            )
            if selected_project:
                context["report_scope_label"] = str(
                    selected_project.get("proj_short_name") or filters.project_id
                )
            elif len(options["projects"]) == 1:
                context["report_scope_label"] = str(
                    options["projects"][0].get("proj_short_name")
                    or options["projects"][0]["proj_id"]
                )
            elif filters.portfolio:
                context["report_scope_label"] = f"{filters.portfolio} portfolio"
            else:
                context["report_scope_label"] = "All matching projects"
            latest_runs = fetch_schedule_quality_refresh_history(
                limit=1,
                offset=0,
                status="success",
            )
            context["latest_refresh"] = latest_runs[0] if latest_runs else None
        except Exception as error:
            logger.exception("Schedule quality programme overview could not be loaded.")
            context.update(
                {
                    "projects": [],
                    "portfolios": [],
                    "lead_planners": [],
                    "project_statuses": [],
                    "project_states": [],
                    "updated_dates": [],
                    "checks": [],
                    "rows": [],
                    "latest_updated_date": None,
                    "total_points_available": 0,
                    "total_points_achieved": 0,
                    "pass_percent": 0,
                    "pass_rate": 85,
                    "pass_or_fail": "N/A",
                    "latest_refresh": None,
                    "report_scope_label": "No matching projects",
                    "overview_report_error": str(error),
                }
            )
        return context


class ScheduleQualityValidationView(
    ScheduleQualityReportRequiredMixin,
    ScheduleQualityReportFiltersMixin,
    TemplateView,
):
    template_name = "backups/schedule_quality_validation.html"

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            return ["backups/partials/schedule_quality_validation_results.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = self._filters()
        context["validation_filters"] = filters
        page_number = parse_non_negative_int(self.request.GET.get("page"), 1) or 1
        page_size = SCHEDULE_QUALITY_EVIDENCE_PAGE_SIZE
        try:
            filters, options = self._filter_options_for_filters(filters)
            context["validation_filters"] = filters
            # The selected check scopes evidence only. Keep every check in the
            # summary so users can compare the programme and see the selected
            # row in context.
            summary_rows = fetch_validation_summary(replace(filters, check_code=""))
            evidence_rows, evidence_count = fetch_validation_evidence(
                filters,
                limit=page_size,
                offset=(page_number - 1) * page_size,
            )
            total_pages = max(1, (evidence_count + page_size - 1) // page_size)
            if page_number > total_pages:
                page_number = total_pages
                evidence_rows, evidence_count = fetch_validation_evidence(
                    filters,
                    limit=page_size,
                    offset=(page_number - 1) * page_size,
                )
            context.update(options)
            context["summary_rows"] = summary_rows
            context["evidence_rows"] = evidence_rows
            context["validation_totals"] = {
                "checks": len(summary_rows),
                "records_checked": max(
                    (int(row["records_checked"]) for row in summary_rows),
                    default=0,
                ),
                "qualifying_results": sum(
                    int(row["qualifying_results"]) for row in summary_rows
                ),
                "evidence_rows": evidence_count,
            }
            context["evidence_page"] = {
                "number": page_number,
                "total_pages": total_pages,
                "page_range": range(1, total_pages + 1),
                "total_rows": evidence_count,
                "has_previous": page_number > 1,
                "has_next": page_number < total_pages,
                "previous_page_number": page_number - 1,
                "next_page_number": page_number + 1,
                "start_index": ((page_number - 1) * page_size) + 1 if evidence_count else 0,
                "end_index": min(page_number * page_size, evidence_count),
            }
            query = self.request.GET.copy()
            query.pop("page", None)
            context["pagination_query"] = query.urlencode()
            latest_runs = fetch_schedule_quality_refresh_history(
                limit=1,
                offset=0,
                status="success",
            )
            context["latest_refresh"] = latest_runs[0] if latest_runs else None
        except Exception as error:
            logger.exception("Schedule quality validation report could not be loaded.")
            context.update(
                {
                    "projects": [],
                    "portfolios": [],
                    "lead_planners": [],
                    "project_statuses": [],
                    "project_states": [],
                    "updated_dates": [],
                    "checks": [],
                    "summary_rows": [],
                    "evidence_rows": [],
                    "validation_report_error": str(error),
                    "validation_totals": {
                        "checks": 0,
                        "records_checked": 0,
                        "qualifying_results": 0,
                        "evidence_rows": 0,
                    },
                    "evidence_page": {
                        "number": 1,
                        "total_pages": 1,
                        "total_rows": 0,
                    },
                    "latest_refresh": None,
                    "pagination_query": "",
                }
            )
        return context


PORTFOLIO_REPORT_PAGES = (
    {
        "key": "overview",
        "title": "Portfolio Overview",
        "description": "Portfolio health, project status and the projects requiring intervention.",
        "url_name": "portfolio_reporting_overview",
    },
    {
        "key": "milestones",
        "title": "Milestone Governance",
        "description": "Milestone demand, delivery status and overdue exposure by project.",
        "url_name": "portfolio_reporting_milestones",
    },
    {
        "key": "risk_float",
        "title": "Schedule Risk & Float",
        "description": "Current total-float distribution and schedule-risk exposure.",
        "url_name": "portfolio_reporting_risk_float",
    },
    {
        "key": "health",
        "title": "Schedule Health",
        "description": "Configured schedule-quality scores and project health bands.",
        "url_name": "portfolio_reporting_health",
    },
    {
        "key": "project_detail",
        "title": "Project Detail",
        "description": "Progress, float, milestones and critical exposure for one project.",
        "url_name": "portfolio_reporting_project_detail",
    },
    {
        "key": "resources",
        "title": "Resources",
        "description": "Planned, actual and remaining resource demand from P6 assignments.",
        "url_name": "portfolio_reporting_resources",
    },
)


class PortfolioReportingHubView(ScheduleQualityReportRequiredMixin, TemplateView):
    template_name = "backups/portfolio_reporting_hub.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["portfolio_report_pages"] = PORTFOLIO_REPORT_PAGES
        return context


class PortfolioReportingView(
    ScheduleQualityReportRequiredMixin,
    ScheduleQualityReportFiltersMixin,
    TemplateView,
):
    template_name = "backups/portfolio_reporting_report.html"
    report_key = ""
    report_fetcher = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["portfolio_report_pages"] = PORTFOLIO_REPORT_PAGES
        context["active_report"] = next(
            page for page in PORTFOLIO_REPORT_PAGES if page["key"] == self.report_key
        )
        filters = self._filters()
        context["validation_filters"] = filters
        try:
            filters, options = self._filter_options_for_filters(filters)
            context["validation_filters"] = filters
            context.update(options)
            context["report_data"] = self.report_fetcher(filters)
            context["portfolio_report_error"] = ""
        except Exception as error:
            logger.exception("Portfolio report %s could not be loaded.", self.report_key)
            context.update(
                {
                    "projects": [],
                    "portfolios": [],
                    "lead_planners": [],
                    "project_statuses": [],
                    "project_states": [],
                    "updated_dates": [],
                    "report_data": {"kpis": [], "rows": []},
                    "portfolio_report_error": str(error),
                }
            )
        return context


class PortfolioOverviewView(PortfolioReportingView):
    report_key = "overview"
    report_fetcher = staticmethod(fetch_portfolio_overview)


class MilestoneGovernanceView(PortfolioReportingView):
    report_key = "milestones"
    report_fetcher = staticmethod(fetch_milestone_governance)


class ScheduleRiskFloatView(PortfolioReportingView):
    report_key = "risk_float"
    report_fetcher = staticmethod(fetch_schedule_risk_and_float)


class ScheduleHealthView(PortfolioReportingView):
    report_key = "health"
    report_fetcher = staticmethod(fetch_schedule_health)


class PortfolioProjectDetailView(PortfolioReportingView):
    report_key = "project_detail"
    report_fetcher = staticmethod(fetch_project_detail)


class ResourceReportingView(PortfolioReportingView):
    report_key = "resources"
    report_fetcher = staticmethod(fetch_resource_overview)


class TargetDetailView(StaffRequiredMixin, TemplateView):
    template_name = "backups/target_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        target = get_object_or_404(BackupTarget, slug=kwargs["slug"])
        context["target"] = target
        page_obj = Paginator(target.runs.all(), TABLE_PAGE_SIZE).get_page(self.request.GET.get("page"))
        context["recent_runs"] = page_obj.object_list
        context["page_obj"] = page_obj
        context.update(parse_backup_schedule_for_form(target.backup_schedule))
        context["day_labels"] = [
            ("1", "Mon"),
            ("2", "Tue"),
            ("3", "Wed"),
            ("4", "Thu"),
            ("5", "Fri"),
            ("6", "Sat"),
            ("0", "Sun"),
        ]
        return context


class TargetUpdateView(StaffRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, slug: str):
        target = get_object_or_404(BackupTarget, slug=slug)
        schedule = build_schedule_from_post(request)
        if schedule is None:
            messages.error(request, "Choose a valid schedule before saving.")
            return redirect(target)

        backup_url = request.POST.get("backup_url", "").strip()
        sharepoint_site = request.POST.get("sharepoint_site", target.sharepoint_site).strip()
        sharepoint_folder = request.POST.get("sharepoint_folder", target.sharepoint_folder).strip()
        if backup_url:
            parsed = parse_sharepoint_url(backup_url, default_folder=target.sharepoint_folder)
            sharepoint_site = parsed["site"]
            sharepoint_folder = parsed["folder"]

        target.enabled = request.POST.get("enabled") == "on"
        target.backup_schedule = schedule
        target.sql_host = request.POST.get("sql_host", target.sql_host).strip() or target.sql_host
        target.sql_port = int(request.POST.get("sql_port", target.sql_port))
        target.sql_database = request.POST.get("sql_database", target.sql_database).strip() or target.sql_database
        target.sql_username = request.POST.get("sql_username", target.sql_username).strip() or target.sql_username
        target.sql_password_env = request.POST.get("sql_password", target.sql_password_env).strip()
        target.app_backup_dir = request.POST.get("app_backup_dir", target.app_backup_dir).strip() or target.app_backup_dir
        target.sql_backup_dir = request.POST.get("sql_backup_dir", target.sql_backup_dir).strip() or target.sql_backup_dir
        target.sql_data_dir = request.POST.get("sql_data_dir", target.sql_data_dir).strip() or target.sql_data_dir
        target.data_file_path = request.POST.get("data_file_path", "").strip()
        target.log_file_path = request.POST.get("log_file_path", "").strip()
        target.sharepoint_site = sharepoint_site or "root"
        target.sharepoint_folder = sharepoint_folder or "P6 Backups"
        target.notification_emails = request.POST.get("notification_emails", "").strip()
        target.retention_daily = parse_non_negative_int(
            request.POST.get("retention_daily", target.retention_daily),
            target.retention_daily,
        )
        target.retention_weekly = parse_non_negative_int(
            request.POST.get("retention_weekly", target.retention_weekly),
            target.retention_weekly,
        )
        target.retention_monthly = parse_non_negative_int(
            request.POST.get("retention_monthly", target.retention_monthly),
            target.retention_monthly,
        )
        target.use_compression = request.POST.get("use_compression") == "on"
        target.verify_sharepoint_upload = request.POST.get("verify_sharepoint_upload") == "on"
        target.keep_local_files = request.POST.get("keep_local_files") == "on"
        target.save()
        sync_target_schedule(target)

        messages.success(request, f"{target.name} settings saved.")
        return redirect(target)


class TriggerBackupView(StaffRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, slug: str):
        target = get_object_or_404(BackupTarget, slug=slug)
        execute_target_backup.delay(
            target_slug=target.slug,
            force=True,
            trigger=BackupRun.TRIGGER_MANUAL,
        )
        messages.success(request, f"Manual backup queued for {target.name}.")
        return redirect(target)


class TriggerScheduleQualityRefreshView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        execute_schedule_quality_refresh.delay(trigger="manual")
        messages.success(request, "Power BI schedule quality refresh queued.")
        return redirect("schedule_quality_dashboard")


class ScheduleQualityScheduleUpdateView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        if not can_edit_schedule_quality_schedule(request.user):
            raise PermissionDenied

        schedule = build_schedule_from_post(request)
        if schedule is None:
            messages.error(request, "Choose a valid schedule before saving.")
            return redirect("schedule_quality_dashboard")

        sync_schedule_quality_refresh_schedule(
            enabled=request.POST.get("enabled") == "on",
            crontab_string=schedule,
            proj_id=None,
        )
        messages.success(request, "Power BI refresh schedule updated.")
        return redirect("schedule_quality_dashboard")


class ScheduleQualitySettingsView(
    LoginRequiredMixin,
    UserPassesTestMixin,
    TemplateView,
):
    template_name = "backups/schedule_quality_settings.html"
    raise_exception = True

    def test_func(self):
        return can_edit_schedule_quality_settings(self.request.user)

    def _load_draft(self):
        actor = schedule_quality_actor(self.request.user)
        config_version_id = get_or_create_schedule_quality_draft(
            changed_by=actor,
            profile_code=get_schedule_quality_profile_code(),
        )
        return fetch_schedule_quality_settings(config_version_id=config_version_id)

    def _build_context(self, *, snapshot, form):
        return {
            "settings_snapshot": snapshot,
            "settings_form": form,
        }

    def get(self, request, *args, **kwargs):
        snapshot = self._load_draft()
        form = ScheduleQualitySettingsForm(settings_snapshot=snapshot)
        return self.render_to_response(self._build_context(snapshot=snapshot, form=form))

    def post(self, request, *args, **kwargs):
        try:
            config_version_id = int(request.POST.get("config_version_id", ""))
            if config_version_id < 1:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, "The settings version is invalid. Reload and try again.")
            return redirect("schedule_quality_settings")

        snapshot = fetch_schedule_quality_settings(
            config_version_id=config_version_id,
        )
        if (
            snapshot.profile_code != get_schedule_quality_profile_code()
            or snapshot.state != "draft"
        ):
            messages.error(
                request,
                "That draft is no longer editable. Reload to use the current settings.",
            )
            return redirect("schedule_quality_settings")

        form = ScheduleQualitySettingsForm(
            request.POST,
            settings_snapshot=snapshot,
        )
        action = request.POST.get("action", "save")
        if action not in {"save", "publish"}:
            form.add_error(None, "Choose Save draft or Publish and rebuild.")
        if not form.is_valid():
            return self.render_to_response(
                self._build_context(snapshot=snapshot, form=form),
                status=400,
            )

        payload = form.build_payload()
        change_note = build_schedule_quality_change_note(snapshot, payload)
        actor = schedule_quality_actor(request.user)
        try:
            saved_settings_hash = save_schedule_quality_draft(
                config_version_id=config_version_id,
                payload=payload,
                expected_settings_hash=form.cleaned_data["expected_settings_hash"],
                changed_by=actor,
                change_note=change_note,
            )
        except ScheduleQualitySettingsConflict:
            form.add_error(
                None,
                "This draft changed while you were editing it. Reload before saving or publishing.",
            )
            return self.render_to_response(
                self._build_context(snapshot=snapshot, form=form),
                status=409,
            )
        except Exception:
            logger.exception("Schedule quality draft %s could not be saved.", config_version_id)
            form.add_error(
                None,
                "The draft could not be saved. No settings were published; check the server logs.",
            )
            return self.render_to_response(
                self._build_context(snapshot=snapshot, form=form),
                status=502,
            )

        if action == "publish":
            try:
                publish_schedule_quality_config_task.delay(
                    config_version_id=config_version_id,
                    expected_settings_hash=saved_settings_hash,
                    published_by=actor,
                )
            except Exception:
                logger.exception(
                    "Schedule quality draft %s could not be queued for publishing.",
                    config_version_id,
                )
                form.add_error(
                    None,
                    "The draft was saved, but the publish and rebuild could not be queued. "
                    "The active settings and results were not changed; check the server logs.",
                )
                return self.render_to_response(
                    self._build_context(snapshot=snapshot, form=form),
                    status=502,
                )
            messages.success(
                request,
                "Schedule quality settings saved. Publish and rebuild queued; "
                "follow its status in Refresh History.",
            )
        else:
            messages.success(request, "Schedule quality settings saved as a draft.")
        return redirect("schedule_quality_settings")


class ScheduleQualityP6FieldsApiView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        from django.http import JsonResponse
        from backups.services.p6_schema_discovery import (
            discover_p6_fields,
            discover_p6_schema,
            discover_p6_tables,
        )

        if request.GET.get("category") == "tables":
            return JsonResponse({"tables": discover_p6_tables()})
        if request.GET.get("category") == "schema":
            return JsonResponse({"tables": discover_p6_schema()})
        return JsonResponse({"fields": discover_p6_fields(request.GET.get("category", ""))})


class MaintenanceDashboardView(StaffRequiredMixin, TemplateView):
    template_name = "backups/maintenance_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_type = self.request.GET.get("run_type", "")
        successful_runs = DatabaseMaintenanceRun.objects.filter(
            status=DatabaseMaintenanceRun.STATUS_SUCCESS
        )
        session_items_count = (
            successful_runs.filter(
                run_type=DatabaseMaintenanceRun.TYPE_STALE_SESSION
            ).aggregate(total=Sum("items_processed_count"))["total"]
            or 0
        )
        log_items_count = (
            successful_runs.filter(
                run_type=DatabaseMaintenanceRun.TYPE_LOG_PRUNING
            ).aggregate(total=Sum("items_processed_count"))["total"]
            or 0
        )
        total_mb_freed = 0.0
        userdata_runs = successful_runs.filter(
            run_type=DatabaseMaintenanceRun.TYPE_USERDATA_PRUNE
        ).values_list("metrics_summary", "log_output")
        for metrics_summary, log_output in userdata_runs:
            for source in {metrics_summary or "", log_output or ""}:
                match = USERDATA_FREED_CAPACITY_PATTERN.search(source)
                if not match:
                    continue
                amount = float(match.group(1))
                total_mb_freed += amount * 1024 if match.group(2).upper() == "GB" else amount
                break

        runs = DatabaseMaintenanceRun.objects.all()
        if selected_type in dict(DatabaseMaintenanceRun.TYPE_CHOICES):
            runs = runs.filter(run_type=selected_type)
        page_obj = Paginator(runs.prefetch_related("details"), TABLE_PAGE_SIZE).get_page(self.request.GET.get("page"))
        context.update(
            {
                "runs": page_obj,
                "page_obj": page_obj,
                "selected_type": selected_type,
                "defrag_runs_count": successful_runs.filter(
                    run_type=DatabaseMaintenanceRun.TYPE_INDEX_DEFRAG
                ).count(),
                "session_items_count": session_items_count,
                "log_items_count": log_items_count,
                "total_mb_freed": f"{total_mb_freed:,.2f}",
                "total_gb_freed": f"{total_mb_freed / 1024:,.2f}",
            }
        )
        return context


class RemoteBackupListView(StaffRequiredMixin, TemplateView):
    template_name = "backups/remote_backups.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        target = get_object_or_404(BackupTarget, slug=kwargs["slug"])
        context["target"] = target
        confirmation_word = secrets.choice(RESTORE_CONFIRMATION_WORDS)
        self.request.session[restore_confirmation_session_key(target)] = confirmation_word
        context["restore_confirmation_word"] = confirmation_word
        context["backups"] = []
        try:
            client = build_sharepoint_client(
                sharepoint_site=target.sharepoint_site,
                sharepoint_folder=target.sharepoint_folder,
            )
            context["backups"] = build_sharepoint_backup_rows(
                client.list_folder_children(),
                target_slug=target.slug,
            )
        except Exception as error:
            context["error"] = str(error)
        return context


class TriggerRestoreView(StaffRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, slug: str):
        target = get_object_or_404(BackupTarget, slug=slug)
        if not request.user.is_superuser:
            messages.error(request, "Only superusers can queue a database restore.")
            return redirect(reverse("backup_target_remote", kwargs={"slug": target.slug}))

        confirmation = request.POST.get("confirmation_word", "").strip().lower()
        expected_confirmation = request.session.get(restore_confirmation_session_key(target), "")
        if not expected_confirmation or not secrets.compare_digest(confirmation, expected_confirmation):
            messages.error(request, "Type the confirmation word exactly to queue the restore.")
            return redirect(reverse("backup_target_remote", kwargs={"slug": target.slug}))

        execute_target_restore.delay(
            target_slug=target.slug,
            download_url=request.POST.get("download_url", ""),
            file_name=request.POST.get("file_name", ""),
            manifest_download_url=request.POST.get("manifest_download_url", ""),
            confirmation_database=target.sql_database,
            trigger=BackupRun.TRIGGER_MANUAL,
        )
        request.session.pop(restore_confirmation_session_key(target), None)
        messages.success(request, f"Restore queued for {target.name}.")
        return redirect(reverse("backup_target_remote", kwargs={"slug": target.slug}))
