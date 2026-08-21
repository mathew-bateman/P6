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

METRIC_COLUMNS = tuple(
    dict.fromkeys(column for pair in CHECK_METRICS.values() for column in pair)
)


def _score_check(
    *,
    limit_type: str,
    green_limit: Decimal,
    amber_limit: Decimal,
    green_points: int,
    amber_points: int,
    records_checked: int,
    qualifying_results: int,
) -> tuple[Decimal, str, str, int]:
    if limit_type == "Number":
        result_value = Decimal(qualifying_results)
        qualifying_display = str(qualifying_results)
    else:
        result_value = (
            Decimal(qualifying_results) * Decimal("100") / Decimal(records_checked)
            if records_checked
            else Decimal("0")
        )
        qualifying_display = f"{result_value:.2f}%"

    if result_value <= green_limit:
        return result_value, qualifying_display, "Green", green_points
    if result_value <= amber_limit:
        return result_value, qualifying_display, "Amber", amber_points
    return result_value, qualifying_display, "Red", 0

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
        project.[Project State] AS project_state
    FROM [powerbitables].[xertoolkit_vw_PBI_Projects] AS project
    GROUP BY
        project.proj_id,
        project.proj_short_name,
        project.[Updated Date],
        project.[Lead Planner],
        project.[Account],
        project.[Project Status],
        project.[Project State]
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
        "updated_dates": sorted(
            {row["updated_date"] for row in projects if row.get("updated_date")},
            reverse=True,
        ),
        "checks": checks,
    }


def _fetch_scorecard_rows(
    filters: ScheduleQualityValidationFilters,
) -> tuple[list[dict[str, object]], object]:
    target = build_schedule_quality_target()
    where_sql, parameters = _validation_where(filters)
    rows = fetch_rows(
        target,
        PROJECT_DIMENSIONS_CTE
        + f"""
        SELECT
            result.check_code,
            MAX(result.display_name) AS display_name,
            MAX(result.sort_order) AS sort_order,
            MAX(result.limit_type) AS limit_type,
            MAX(result.green_limit) AS green_limit,
            MAX(result.amber_limit) AS amber_limit,
            MAX(result.green_points) AS green_points,
            MAX(result.amber_points) AS amber_points,
            COALESCE(SUM(result.records_checked), 0) AS records_checked,
            COALESCE(SUM(result.qualifying_results), 0) AS qualifying_results,
            MAX(CONVERT(date, project.updated_date)) AS latest_updated_date
        FROM [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityResults] AS result
        JOIN project_dimensions AS project
          ON project.proj_id = result.proj_id
        {where_sql}{" AND" if where_sql else " WHERE"} (? = '' OR result.check_code = ?)
        GROUP BY result.check_code
        ORDER BY MAX(result.sort_order), result.check_code;
        """,
        parameters=parameters + (filters.check_code, filters.check_code),
        database=target.sql_database,
    )
    latest_updated_date = next(
        (row.get("latest_updated_date") for row in rows if row.get("latest_updated_date")),
        None,
    )
    return rows, latest_updated_date


def fetch_validation_summary(
    filters: ScheduleQualityValidationFilters,
) -> list[dict[str, object]]:
    checks, _ = _fetch_scorecard_rows(filters)
    rows: list[dict[str, object]] = []
    for check in checks:
        check_code = str(check["check_code"])
        records_checked = int(check.get("records_checked") or 0)
        qualifying_results = int(check.get("qualifying_results") or 0)
        qualifying_percent = (
            (Decimal(qualifying_results) * Decimal("100") / Decimal(records_checked))
            if records_checked
            else Decimal("0")
        )
        qualifying_display = f"{qualifying_percent:.2f}%"
        _, _, result, points_scored = _score_check(
            limit_type=str(check["limit_type"]),
            green_limit=Decimal(str(check["green_limit"])),
            amber_limit=Decimal(str(check["amber_limit"])),
            green_points=int(check["green_points"]),
            amber_points=int(check["amber_points"]),
            records_checked=records_checked,
            qualifying_results=qualifying_results,
        )
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
                "score_result": result,
                "limit_type": str(check["limit_type"]),
                "green_limit": Decimal(str(check["green_limit"])),
                "amber_limit": Decimal(str(check["amber_limit"])),
                "green_points": int(check["green_points"]),
                "amber_points": int(check["amber_points"]),
                "points_scored": points_scored,
            }
        )
    return rows


def fetch_programme_overview(
    filters: ScheduleQualityValidationFilters,
) -> dict[str, object]:
    # This must remain on ProjectMetrics.  ScheduleQualityResults expands each
    # project into every enabled check and has repeatedly exceeded the public
    # proxy timeout when aggregating the full programme.
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
        SELECT scope.check_code, scope.display_name, scope.sort_order
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
    rows: list[dict[str, object]] = []
    for check in checks:
        check_code = str(check["check_code"])
        metric_pair = CHECK_METRICS.get(check_code)
        if metric_pair is None:
            continue
        records_column, qualifying_column = metric_pair
        records_checked = int(values.get(records_column) or 0)
        qualifying_results = int(values.get(qualifying_column) or 0)
        qualifying_percent = (
            Decimal(qualifying_results) * Decimal("100") / Decimal(records_checked)
            if records_checked
            else Decimal("0")
        )
        rows.append(
            {
                "number": len(rows) + 1,
                "check_code": check_code,
                "description": str(check["display_name"]),
                "records_checked": records_checked,
                "qualifying_results": qualifying_results,
                "qualifying_display": f"{qualifying_percent:.2f}%",
            }
        )

    return {
        "rows": rows,
        "latest_updated_date": values.get("latest_updated_date"),
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
