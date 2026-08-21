from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from backups.services.mssql import fetch_rows
from backups.services.schedule_quality import build_schedule_quality_target


@dataclass(frozen=True)
class ScheduleQualityValidationFilters:
    project_id: int | None = None
    portfolio: str = ""
    lead_planner: str = ""
    project_status: str = ""
    project_state: str = ""
    exclude_blanks: bool = False
    updated_date: date | None = None
    check_code: str = ""
    discipline: str = ""
    project_phase: str = ""


@dataclass(frozen=True)
class ProgrammeCheckRule:
    check_code: str
    display_name: str
    green_limit: Decimal
    amber_limit: Decimal
    limit_type: str
    green_points: int
    amber_points: int


CHECK_METRICS = {
    "missing_predecessor": ("dcma_activity_count", "missing_predecessor_count"),
    "missing_successor": ("dcma_activity_count", "missing_successor_count"),
    "open_finish": ("dcma_activity_count", "open_finish_count"),
    "open_start": ("dcma_activity_count", "open_start_count"),
    "relationship_leads": ("relationship_count", "lead_count"),
    "relationship_lags": ("relationship_count", "lag_count"),
    "relationship_ratio": ("relationship_count", "non_fs_count"),
    "constraints": ("dcma_activity_count", "constraint_count"),
    "high_float": ("dcma_activity_count", "high_float_count"),
    "negative_float": ("dcma_activity_count", "negative_float_count"),
    "high_duration": ("dcma_activity_count", "high_duration_count"),
    "invalid_dates": ("dcma_activity_count", "invalid_date_count"),
    "in_progress_errors": ("dcma_activity_count", "in_progress_error_count"),
    "logical_loops": ("dcma_activity_count", "logical_loop_count"),
    "out_of_sequence": ("relationship_count", "out_of_sequence_count"),
    "critical_tasks": ("dcma_activity_count", "critical_task_count"),
    "near_critical_tasks": ("dcma_activity_count", "near_critical_task_count"),
    "riding_progress_date": ("dcma_activity_count", "riding_progress_date_count"),
    "excessive_ss_lag": ("relationship_count", "excessive_ss_lag_count"),
    "excessive_ff_lag": ("relationship_count", "excessive_ff_lag_count"),
}

PROGRAMME_PASS_RATE = Decimal("85.00")
PROGRAMME_CHECK_RULES = (
    ProgrammeCheckRule("logical_loops", "Logical Loops", Decimal("0"), Decimal("1"), "Number", 50, 40),
    ProgrammeCheckRule("out_of_sequence", "Out of Sequence", Decimal("0"), Decimal("0"), "Number", 10, 8),
    ProgrammeCheckRule("missing_predecessor", "Missing Predecessors", Decimal("3"), Decimal("7"), "Percent", 40, 32),
    ProgrammeCheckRule("missing_successor", "Missing Successors", Decimal("3"), Decimal("7"), "Percent", 40, 32),
    ProgrammeCheckRule("relationship_leads", "Relationship +ve Lags (Leads)", Decimal("0"), Decimal("0"), "Percent", 15, 12),
    ProgrammeCheckRule("relationship_lags", "Relationship +ve Lags", Decimal("0"), Decimal("0"), "Percent", 10, 8),
    ProgrammeCheckRule("high_duration", "High Duration", Decimal("3"), Decimal("7"), "Percent", 10, 8),
    ProgrammeCheckRule("high_float", "High Total Float", Decimal("3"), Decimal("7"), "Percent", 10, 8),
    ProgrammeCheckRule("negative_float", "Negative Float", Decimal("0"), Decimal("3"), "Percent", 20, 16),
    ProgrammeCheckRule("constraints", "Constraints", Decimal("3"), Decimal("7"), "Percent", 15, 12),
    ProgrammeCheckRule("open_start", "Open-Start Tasks", Decimal("3"), Decimal("7"), "Percent", 10, 8),
    ProgrammeCheckRule("open_finish", "Open-Finish Tasks", Decimal("3"), Decimal("7"), "Percent", 20, 16),
    ProgrammeCheckRule("critical_tasks", "Critical Tasks", Decimal("30"), Decimal("50"), "Percent", 15, 12),
    ProgrammeCheckRule("near_critical_tasks", "Near Critical Tasks", Decimal("45"), Decimal("66"), "Percent", 15, 12),
    ProgrammeCheckRule("invalid_dates", "Invalid Dates", Decimal("0"), Decimal("0"), "Percent", 15, 12),
    ProgrammeCheckRule("in_progress_errors", "In Progress Errors", Decimal("0"), Decimal("0"), "Percent", 15, 12),
    ProgrammeCheckRule("riding_progress_date", "Riding Progress Date", Decimal("3"), Decimal("7"), "Percent", 15, 12),
    ProgrammeCheckRule("excessive_ss_lag", "Excessive SS Lag Duration", Decimal("3"), Decimal("7"), "Percent", 0, 0),
    ProgrammeCheckRule("excessive_ff_lag", "Excessive FF Lag Duration", Decimal("3"), Decimal("7"), "Percent", 0, 0),
    ProgrammeCheckRule("relationship_ratio", "Relationship Ratio", Decimal("3"), Decimal("7"), "Percent", 10, 8),
)

METRIC_COLUMNS = tuple(
    dict.fromkeys(column for pair in CHECK_METRICS.values() for column in pair)
)

PROJECT_DIMENSIONS_CTE = """
WITH project_dimensions AS
(
    SELECT
        project.proj_id,
        project.proj_short_name,
        project.[Updated Date] AS updated_date,
        project.[Lead Planner] AS lead_planner,
        project.[Account] AS portfolio,
        project.[Project Status] AS project_status,
        project.[Project State] AS project_state,
        project.[Industry] AS discipline,
        project.[Project Type] AS project_phase
    FROM [powerbitables].[xertoolkit_vw_PBI_Projects] AS project
    GROUP BY
        project.proj_id,
        project.proj_short_name,
        project.[Updated Date],
        project.[Lead Planner],
        project.[Account],
        project.[Project Status],
        project.[Project State],
        project.[Industry],
        project.[Project Type]
)
"""


def _validation_where(
    filters: ScheduleQualityValidationFilters,
    *,
    project_alias: str = "project",
    evidence_alias: str | None = None,
) -> tuple[str, tuple[object, ...]]:
    clauses: list[str] = []
    parameters: list[object] = []
    if filters.project_id is not None:
        clauses.append(f"{project_alias}.proj_id = ?")
        parameters.append(filters.project_id)
    if filters.portfolio:
        clauses.append(f"{project_alias}.portfolio = ?")
        parameters.append(filters.portfolio)
    if filters.lead_planner:
        clauses.append(f"{project_alias}.lead_planner = ?")
        parameters.append(filters.lead_planner)
    if filters.project_status:
        clauses.append(f"{project_alias}.project_status = ?")
        parameters.append(filters.project_status)
    if filters.project_state:
        clauses.append(f"{project_alias}.project_state = ?")
        parameters.append(filters.project_state)
    if filters.exclude_blanks:
        for field in ("portfolio", "lead_planner", "project_status", "project_state"):
            clauses.append(
                f"NULLIF(LTRIM(RTRIM({project_alias}.{field})), '') IS NOT NULL"
            )
    if filters.updated_date is not None:
        clauses.append(f"CONVERT(date, {project_alias}.updated_date) = ?")
        parameters.append(filters.updated_date)
    if filters.discipline:
        clauses.append(f"{project_alias}.discipline = ?")
        parameters.append(filters.discipline)
    if filters.project_phase:
        clauses.append(f"{project_alias}.project_phase = ?")
        parameters.append(filters.project_phase)
    if evidence_alias and filters.check_code:
        clauses.append(f"{evidence_alias}.check_code = ?")
        parameters.append(filters.check_code)
    return (" WHERE " + " AND ".join(clauses) if clauses else "", tuple(parameters))


def fetch_validation_filter_options() -> dict[str, list[object]]:
    target = build_schedule_quality_target()
    projects = fetch_rows(
        target,
        PROJECT_DIMENSIONS_CTE
        + """
        SELECT
            proj_id,
            proj_short_name,
            portfolio,
            lead_planner,
            project_status,
            project_state,
            discipline,
            project_phase,
            updated_date
        FROM project_dimensions
        ORDER BY proj_short_name, proj_id;
        """,
        database=target.sql_database,
    )
    checks = fetch_rows(
        target,
        """
        SELECT scope.check_code, scope.display_name, scope.sort_order
        FROM [powerbitables].[xertoolkit_schedule_quality_profile] AS profile
        JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
          ON scope.config_version_id = profile.active_config_version_id
        WHERE profile.profile_code = ?
          AND scope.is_enabled = 1
        ORDER BY scope.sort_order, scope.check_code;
        """,
        parameters=("default",),
        database=target.sql_database,
    )
    return {
        "projects": projects,
        "portfolios": sorted(
            {str(row["portfolio"]) for row in projects if row.get("portfolio")}
        ),
        "lead_planners": sorted(
            {str(row["lead_planner"]) for row in projects if row.get("lead_planner")}
        ),
        "project_statuses": sorted(
            {str(row["project_status"]) for row in projects if row.get("project_status")}
        ),
        "project_states": sorted(
            {str(row["project_state"]) for row in projects if row.get("project_state")}
        ),
        "disciplines": sorted(
            {str(row["discipline"]) for row in projects if row.get("discipline")}
        ),
        "project_phases": sorted(
            {str(row["project_phase"]) for row in projects if row.get("project_phase")}
        ),
        "updated_dates": sorted(
            {row["updated_date"] for row in projects if row.get("updated_date")},
            reverse=True,
        ),
        "checks": checks,
    }


def fetch_validation_summary(
    filters: ScheduleQualityValidationFilters,
) -> list[dict[str, object]]:
    target = build_schedule_quality_target()
    where_sql, parameters = _validation_where(filters)
    aggregate_select = ",\n".join(
        f"            COALESCE(SUM(CONVERT(bigint, metrics.{column})), 0) AS {column}"
        for column in METRIC_COLUMNS
    )
    aggregate = fetch_rows(
        target,
        PROJECT_DIMENSIONS_CTE
        + f"""
        SELECT
{aggregate_select}
        FROM [powerbitables].[xertoolkit_vw_PBI_ProjectMetrics] AS metrics
        JOIN project_dimensions AS project
          ON project.proj_id = metrics.proj_id
        {where_sql};
        """,
        parameters=parameters,
        database=target.sql_database,
    )
    checks = fetch_rows(
        target,
        """
        SELECT scope.check_code, scope.display_name, scope.sort_order
        FROM [powerbitables].[xertoolkit_schedule_quality_profile] AS profile
        JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
          ON scope.config_version_id = profile.active_config_version_id
        WHERE profile.profile_code = ?
          AND scope.is_enabled = 1
        ORDER BY scope.sort_order, scope.check_code;
        """,
        parameters=("default",),
        database=target.sql_database,
    )
    values = aggregate[0] if aggregate else {}
    rows: list[dict[str, object]] = []
    for check in checks:
        check_code = str(check["check_code"])
        if filters.check_code and check_code != filters.check_code:
            continue
        metric_pair = CHECK_METRICS.get(check_code)
        if not metric_pair:
            continue
        records_column, qualifying_column = metric_pair
        records_checked = int(values.get(records_column) or 0)
        qualifying_results = int(values.get(qualifying_column) or 0)
        qualifying_percent = (
            (Decimal(qualifying_results) * Decimal("100") / Decimal(records_checked))
            if records_checked
            else Decimal("0")
        )
        qualifying_display = f"{qualifying_percent:.2f}%"
        rows.append(
            {
                "number": len(rows) + 1,
                "check_code": check_code,
                "check_name": str(check["display_name"]),
                "records_checked": records_checked,
                "qualifying_results": qualifying_results,
                "qualifying_percent": qualifying_percent.quantize(Decimal("0.01")),
                "qualifying_display": qualifying_display,
                "status": "review" if qualifying_results else "clear",
            }
        )
    return rows


def fetch_programme_overview(
    filters: ScheduleQualityValidationFilters,
) -> dict[str, object]:
    target = build_schedule_quality_target()
    where_sql, parameters = _validation_where(filters)
    aggregate_select = ",\n".join(
        f"            COALESCE(SUM(CONVERT(bigint, metrics.{column})), 0) AS {column}"
        for column in METRIC_COLUMNS
    )
    aggregate = fetch_rows(
        target,
        PROJECT_DIMENSIONS_CTE
        + f"""
        SELECT
{aggregate_select},
            MAX(CONVERT(date, project.updated_date)) AS latest_updated_date
        FROM [powerbitables].[xertoolkit_vw_PBI_ProjectMetrics] AS metrics
        JOIN project_dimensions AS project
          ON project.proj_id = metrics.proj_id
        {where_sql};
        """,
        parameters=parameters,
        database=target.sql_database,
    )
    checks = fetch_rows(
        target,
        """
        SELECT scope.check_code, scope.display_name
        FROM [powerbitables].[xertoolkit_schedule_quality_profile] AS profile
        JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
          ON scope.config_version_id = profile.active_config_version_id
        WHERE profile.profile_code = ?
          AND scope.is_enabled = 1;
        """,
        parameters=("default",),
        database=target.sql_database,
    )
    values = aggregate[0] if aggregate else {}
    active_names = {
        str(check["check_code"]): str(check["display_name"])
        for check in checks
    }
    rows: list[dict[str, object]] = []
    for rule in PROGRAMME_CHECK_RULES:
        if rule.check_code not in active_names:
            continue
        records_column, qualifying_column = CHECK_METRICS[rule.check_code]
        records_checked = int(values.get(records_column) or 0)
        qualifying_count = int(values.get(qualifying_column) or 0)
        if rule.limit_type == "Number":
            result_value = Decimal(qualifying_count)
            qualifying_results = qualifying_count
            qualifying_display = str(qualifying_count)
        else:
            result_value = (
                Decimal(qualifying_count) * Decimal("100") / Decimal(records_checked)
                if records_checked
                else Decimal("0")
            )
            qualifying_results = qualifying_count
            qualifying_display = f"{result_value:.2f}%"

        if result_value <= rule.green_limit:
            result = "Green"
            points_scored = rule.green_points
        elif result_value <= rule.amber_limit:
            result = "Amber"
            points_scored = rule.amber_points
        else:
            result = "Red"
            points_scored = 0
        rows.append(
            {
                "number": len(rows) + 1,
                "check_code": rule.check_code,
                "description": active_names.get(rule.check_code, rule.display_name),
                "result": result,
                "records_checked": records_checked,
                "qualifying_results": qualifying_results,
                "qualifying_display": qualifying_display,
                "green_limit": rule.green_limit,
                "amber_limit": rule.amber_limit,
                "limit_type": rule.limit_type,
                "green_points": rule.green_points,
                "amber_points": rule.amber_points,
                "points_scored": points_scored,
            }
        )

    total_points_available = sum(int(row["green_points"]) for row in rows)
    total_points_achieved = sum(int(row["points_scored"]) for row in rows)
    pass_percent = (
        Decimal(total_points_achieved) * Decimal("100") / Decimal(total_points_available)
        if total_points_available
        else Decimal("0")
    ).quantize(Decimal("0.01"))
    return {
        "rows": rows,
        "latest_updated_date": values.get("latest_updated_date"),
        "total_points_available": total_points_available,
        "total_points_achieved": total_points_achieved,
        "pass_percent": pass_percent,
        "pass_rate": PROGRAMME_PASS_RATE,
        "pass_or_fail": "PASS" if pass_percent >= PROGRAMME_PASS_RATE else "FAIL",
    }


def fetch_validation_evidence(
    filters: ScheduleQualityValidationFilters,
    *,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[dict[str, object]], int]:
    target = build_schedule_quality_target()
    safe_limit = max(1, min(int(limit), 100))
    safe_offset = max(0, int(offset))
    where_sql, parameters = _validation_where(
        filters,
        evidence_alias="evidence",
    )
    from_sql = f"""
        FROM [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence] AS evidence
        JOIN project_dimensions AS project
          ON project.proj_id = evidence.proj_id
        {where_sql}
    """
    count_rows = fetch_rows(
        target,
        PROJECT_DIMENSIONS_CTE
        + "SELECT COUNT_BIG(*) AS row_count\n"
        + from_sql
        + ";",
        parameters=parameters,
        database=target.sql_database,
    )
    rows = fetch_rows(
        target,
        PROJECT_DIMENSIONS_CTE
        + """
        SELECT
            project.lead_planner,
            project.proj_short_name,
            evidence.proj_id,
            evidence.check_code,
            evidence.check_name,
            evidence.task_id,
            evidence.task_code,
            evidence.task_name,
            evidence.evidence_display,
            evidence.refreshed_at
        """
        + from_sql
        + """
        ORDER BY
            evidence.check_sort_order,
            project.proj_short_name,
            evidence.task_code,
            evidence.task_id
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY;
        """,
        parameters=(*parameters, safe_offset, safe_limit),
        database=target.sql_database,
    )
    total = int(count_rows[0]["row_count"]) if count_rows else 0
    return rows, total
