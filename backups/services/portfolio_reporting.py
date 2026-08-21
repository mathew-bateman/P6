from __future__ import annotations

from decimal import Decimal

from backups.services.mssql import fetch_rows
from backups.services.schedule_quality import build_schedule_quality_target
from backups.services.schedule_quality_reporting import (
    CHECK_METRICS,
    PROGRAMME_CHECK_RULES,
    ScheduleQualityValidationFilters,
)


PROJECT_REPORTING_CTE = """
WITH project_dimensions AS
(
    SELECT
        project.proj_id,
        project.proj_short_name,
        project.data_date,
        project.[Updated Date] AS updated_date,
        project.[Lead Planner] AS lead_planner,
        project.[Account] AS portfolio,
        project.[Project Status] AS project_status,
        project.[Project State] AS project_state,
        project.[Industry] AS discipline,
        project.[Project Type] AS project_phase
    FROM [powerbitables].[xertoolkit_vw_PBI_Projects] AS project
)
"""


def _where(
    filters: ScheduleQualityValidationFilters,
    *,
    project_alias: str = "project",
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
    if filters.updated_date is not None:
        clauses.append(f"CONVERT(date, {project_alias}.updated_date) = ?")
        parameters.append(filters.updated_date)
    return (" WHERE " + " AND ".join(clauses) if clauses else "", tuple(parameters))


def _number(value: object) -> int:
    return int(value or 0)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value or 0))


def _score_project(row: dict[str, object]) -> dict[str, object]:
    points_available = 0
    points_achieved = 0
    for rule in PROGRAMME_CHECK_RULES:
        records_column, qualifying_column = CHECK_METRICS[rule.check_code]
        records = _number(row.get(records_column))
        qualifying = _number(row.get(qualifying_column))
        if rule.limit_type == "Number":
            result = Decimal(qualifying)
        else:
            result = Decimal(qualifying) * Decimal("100") / Decimal(records) if records else Decimal("0")
        points_available += rule.green_points
        if result <= rule.green_limit:
            points_achieved += rule.green_points
        elif result <= rule.amber_limit:
            points_achieved += rule.amber_points
    score = (
        Decimal(points_achieved) * Decimal("100") / Decimal(points_available)
        if points_available
        else Decimal("0")
    ).quantize(Decimal("0.01"))
    band = "Good" if score >= 90 else "Fair" if score >= 80 else "Poor"
    return {
        **row,
        "health_score": score,
        "health_band": band,
        "points_available": points_available,
        "points_achieved": points_achieved,
    }


def _project_metric_rows(filters: ScheduleQualityValidationFilters) -> list[dict[str, object]]:
    target = build_schedule_quality_target()
    where_sql, parameters = _where(filters)
    metric_columns = ",\n            ".join(f"metrics.{column}" for column in dict.fromkeys(
        column for pair in CHECK_METRICS.values() for column in pair
    ))
    rows = fetch_rows(
        target,
        PROJECT_REPORTING_CTE
        + f"""
        SELECT
            project.proj_id,
            project.proj_short_name,
            project.portfolio,
            project.lead_planner,
            project.project_status,
            project.project_state,
            project.discipline,
            project.project_phase,
            project.updated_date,
            {metric_columns}
        FROM [powerbitables].[xertoolkit_vw_PBI_ProjectMetrics] AS metrics
        JOIN project_dimensions AS project
          ON project.proj_id = metrics.proj_id
        {where_sql}
        ORDER BY project.proj_short_name, project.proj_id;
        """,
        parameters=parameters,
        database=target.sql_database,
    )
    return [_score_project(row) for row in rows]


def fetch_portfolio_overview(filters: ScheduleQualityValidationFilters) -> dict[str, object]:
    projects = _project_metric_rows(filters)
    total = len(projects)
    average = (
        sum((_decimal(row["health_score"]) for row in projects), Decimal("0")) / Decimal(total)
        if total
        else Decimal("0")
    ).quantize(Decimal("0.01"))
    bands = {band: sum(1 for row in projects if row["health_band"] == band) for band in ("Good", "Fair", "Poor")}
    top_projects = sorted(
        projects,
        key=lambda row: (
            _decimal(row["health_score"]),
            -_number(row.get("negative_float_count")),
            -_number(row.get("critical_task_count")),
        ),
    )[:10]
    statuses: dict[str, int] = {}
    for row in projects:
        label = str(row.get("project_status") or "Not recorded")
        statuses[label] = statuses.get(label, 0) + 1
    status_rows = [
        {"label": label, "count": count, "percent": round(count * 100 / total, 1) if total else 0}
        for label, count in sorted(statuses.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "project_rows": projects,
        "top_projects": top_projects,
        "status_rows": status_rows,
        "kpis": [
            {"label": "Live projects", "value": total, "detail": "Projects matching the current filters"},
            {"label": "Projects good", "value": bands["Good"], "detail": "Health score of 90% or above", "tone": "good"},
            {"label": "Projects fair", "value": bands["Fair"], "detail": "Health score from 80% to 89.99%", "tone": "fair"},
            {"label": "Projects poor", "value": bands["Poor"], "detail": "Health score below 80%", "tone": "poor"},
            {"label": "Average health", "value": f"{average}%", "detail": "Average configured schedule-health score"},
            {"label": "Critical activities", "value": sum(_number(row.get("critical_task_count")) for row in projects), "detail": "Across the selected projects"},
            {"label": "Negative float", "value": sum(_number(row.get("negative_float_count")) for row in projects), "detail": "Activities currently reporting negative float", "tone": "poor"},
            {"label": "Near critical", "value": sum(_number(row.get("near_critical_task_count")) for row in projects), "detail": "Activities within the configured near-critical range"},
        ],
    }


def fetch_milestone_governance(filters: ScheduleQualityValidationFilters) -> dict[str, object]:
    target = build_schedule_quality_target()
    where_sql, parameters = _where(filters)
    from_sql = f"""
        FROM [powerbitables].[xertoolkit_vw_PBI_Activities] AS activity
        JOIN project_dimensions AS project ON project.proj_id = activity.proj_id
        {where_sql}{' AND' if where_sql else ' WHERE'} activity.is_milestone = 1
          AND activity.is_deleted = 0
    """
    status_sql = """
        COUNT_BIG(*) AS total_milestones,
        SUM(CASE WHEN activity.is_complete = 1 AND (activity.target_end_date IS NULL OR activity.act_end_date <= activity.target_end_date) THEN 1 ELSE 0 END) AS on_time,
        SUM(CASE WHEN activity.is_complete = 0 AND COALESCE(activity.target_end_date, activity.early_end_date) < project.data_date THEN 1 ELSE 0 END) AS overdue,
        SUM(CASE WHEN activity.is_complete = 0 AND COALESCE(activity.target_end_date, activity.early_end_date) >= project.data_date AND COALESCE(activity.target_end_date, activity.early_end_date) < DATEADD(day, 30, project.data_date) THEN 1 ELSE 0 END) AS due_30,
        SUM(CASE WHEN activity.is_complete = 0 AND COALESCE(activity.target_end_date, activity.early_end_date) >= DATEADD(day, 30, project.data_date) AND COALESCE(activity.target_end_date, activity.early_end_date) < DATEADD(day, 60, project.data_date) THEN 1 ELSE 0 END) AS due_60,
        SUM(CASE WHEN activity.is_complete = 0 AND COALESCE(activity.target_end_date, activity.early_end_date) >= project.data_date AND COALESCE(activity.target_end_date, activity.early_end_date) < DATEADD(day, 90, project.data_date) THEN 1 ELSE 0 END) AS due_90
    """
    totals = fetch_rows(
        target,
        PROJECT_REPORTING_CTE + "SELECT\n" + status_sql + from_sql + ";",
        parameters=parameters,
        database=target.sql_database,
    )
    rows = fetch_rows(
        target,
        PROJECT_REPORTING_CTE
        + "SELECT project.proj_id, project.proj_short_name,\n"
        + status_sql
        + from_sql
        + " GROUP BY project.proj_id, project.proj_short_name ORDER BY overdue DESC, project.proj_short_name;",
        parameters=parameters,
        database=target.sql_database,
    )
    values = totals[0] if totals else {}
    total = _number(values.get("total_milestones"))
    for row in rows:
        row["on_time_percent"] = round(_number(row.get("on_time")) * 100 / _number(row.get("total_milestones")), 1) if _number(row.get("total_milestones")) else 0
    return {
        "rows": rows,
        "kpis": [
            {"label": "Total milestones", "value": total, "detail": "Current submitted schedules"},
            {"label": "Milestones on time", "value": _number(values.get("on_time")), "detail": f"{round(_number(values.get('on_time')) * 100 / total, 1) if total else 0}% of milestones", "tone": "good"},
            {"label": "Due next 30 days", "value": _number(values.get("due_30")), "detail": "Measured from each project's data date", "tone": "fair"},
            {"label": "Due next 60 days", "value": _number(values.get("due_60")), "detail": "Days 30 to 59 from each data date"},
            {"label": "Overdue", "value": _number(values.get("overdue")), "detail": "Incomplete and behind target finish", "tone": "poor"},
            {"label": "Due next 90 days", "value": _number(values.get("due_90")), "detail": "Forward milestone demand"},
        ],
        "distribution": [
            {"label": "On time", "value": _number(values.get("on_time")), "tone": "good"},
            {"label": "Due in 30 days", "value": _number(values.get("due_30")), "tone": "fair"},
            {"label": "Due in 60 days", "value": _number(values.get("due_60")), "tone": "neutral"},
            {"label": "Overdue", "value": _number(values.get("overdue")), "tone": "poor"},
        ],
    }


def fetch_schedule_risk_and_float(filters: ScheduleQualityValidationFilters) -> dict[str, object]:
    target = build_schedule_quality_target()
    where_sql, parameters = _where(filters)
    rows = fetch_rows(
        target,
        PROJECT_REPORTING_CTE
        + f"""
        SELECT
            COUNT_BIG(*) AS activity_count,
            SUM(CASE WHEN activity.total_float_days <= 0 THEN 1 ELSE 0 END) AS float_0,
            SUM(CASE WHEN activity.total_float_days > 0 AND activity.total_float_days <= 5 THEN 1 ELSE 0 END) AS float_1_5,
            SUM(CASE WHEN activity.total_float_days > 5 AND activity.total_float_days <= 10 THEN 1 ELSE 0 END) AS float_6_10,
            SUM(CASE WHEN activity.total_float_days > 10 AND activity.total_float_days <= 20 THEN 1 ELSE 0 END) AS float_11_20,
            SUM(CASE WHEN activity.total_float_days > 20 AND activity.total_float_days <= 30 THEN 1 ELSE 0 END) AS float_21_30,
            SUM(CASE WHEN activity.total_float_days > 30 THEN 1 ELSE 0 END) AS float_30_plus,
            AVG(CONVERT(decimal(18,2), activity.total_float_days)) AS average_float
        FROM [powerbitables].[xertoolkit_vw_PBI_Activities] AS activity
        JOIN project_dimensions AS project ON project.proj_id = activity.proj_id
        {where_sql}{' AND' if where_sql else ' WHERE'} activity.is_dcma_activity = 1
          AND activity.is_deleted = 0;
        """,
        parameters=parameters,
        database=target.sql_database,
    )
    values = rows[0] if rows else {}
    distribution = [
        {"label": "0 days or less", "value": _number(values.get("float_0")), "tone": "poor"},
        {"label": "1-5 days", "value": _number(values.get("float_1_5")), "tone": "fair"},
        {"label": "6-10 days", "value": _number(values.get("float_6_10"))},
        {"label": "11-20 days", "value": _number(values.get("float_11_20"))},
        {"label": "21-30 days", "value": _number(values.get("float_21_30"))},
        {"label": "30+ days", "value": _number(values.get("float_30_plus"))},
    ]
    maximum = max((item["value"] for item in distribution), default=0)
    for item in distribution:
        item["width"] = round(item["value"] * 100 / maximum, 1) if maximum else 0
    return {
        "distribution": distribution,
        "kpis": [
            {"label": "Activities measured", "value": _number(values.get("activity_count")), "detail": "Active non-summary schedule activities"},
            {"label": "Zero or negative float", "value": _number(values.get("float_0")), "detail": "Activities with no remaining total float", "tone": "poor"},
            {"label": "Average total float", "value": f"{_decimal(values.get('average_float')).quantize(Decimal('0.01'))} days", "detail": "Current submitted schedules"},
            {"label": "More than 30 days", "value": _number(values.get("float_30_plus")), "detail": "Activities with high available float"},
        ],
    }


def fetch_schedule_health(filters: ScheduleQualityValidationFilters) -> dict[str, object]:
    projects = _project_metric_rows(filters)
    total = len(projects)
    average = (
        sum((_decimal(row["health_score"]) for row in projects), Decimal("0")) / Decimal(total)
        if total else Decimal("0")
    ).quantize(Decimal("0.01"))
    return {
        "rows": sorted(projects, key=lambda row: (_decimal(row["health_score"]), str(row.get("proj_short_name") or ""))),
        "kpis": [
            {"label": "Average health score", "value": f"{average}%", "detail": "Configured schedule-quality score"},
            {"label": "Projects good", "value": sum(1 for row in projects if row["health_band"] == "Good"), "detail": "90-100%", "tone": "good"},
            {"label": "Projects fair", "value": sum(1 for row in projects if row["health_band"] == "Fair"), "detail": "80-89.99%", "tone": "fair"},
            {"label": "Projects poor", "value": sum(1 for row in projects if row["health_band"] == "Poor"), "detail": "Below 80%", "tone": "poor"},
        ],
    }


def fetch_project_detail(filters: ScheduleQualityValidationFilters) -> dict[str, object]:
    if filters.project_id is None:
        return {"project": None, "kpis": [], "activity_status": []}
    projects = _project_metric_rows(filters)
    project = projects[0] if projects else None
    if project is None:
        return {"project": None, "kpis": [], "activity_status": []}
    target = build_schedule_quality_target()
    where_sql, parameters = _where(filters)
    rows = fetch_rows(
        target,
        PROJECT_REPORTING_CTE
        + f"""
        SELECT
            AVG(CONVERT(decimal(18,2), activity.phys_complete_pct)) AS average_complete,
            AVG(CONVERT(decimal(18,2), activity.total_float_days)) AS average_float,
            SUM(CASE WHEN activity.status_code = 'TK_Complete' THEN 1 ELSE 0 END) AS complete_count,
            SUM(CASE WHEN activity.status_code = 'TK_Active' THEN 1 ELSE 0 END) AS in_progress_count,
            SUM(CASE WHEN activity.status_code = 'TK_NotStart' THEN 1 ELSE 0 END) AS not_started_count,
            SUM(CASE WHEN activity.is_milestone = 1 AND activity.is_complete = 0 AND COALESCE(activity.target_end_date, activity.early_end_date) < project.data_date THEN 1 ELSE 0 END) AS late_milestones,
            SUM(CASE WHEN activity.total_float_days < 0 THEN 1 ELSE 0 END) AS negative_float_activities
        FROM [powerbitables].[xertoolkit_vw_PBI_Activities] AS activity
        JOIN project_dimensions AS project ON project.proj_id = activity.proj_id
        {where_sql}{' AND' if where_sql else ' WHERE'} activity.is_deleted = 0;
        """,
        parameters=parameters,
        database=target.sql_database,
    )
    values = rows[0] if rows else {}
    return {
        "project": project,
        "activity_status": [
            {"label": "Complete", "value": _number(values.get("complete_count")), "tone": "good"},
            {"label": "In progress", "value": _number(values.get("in_progress_count")), "tone": "fair"},
            {"label": "Not started", "value": _number(values.get("not_started_count"))},
        ],
        "kpis": [
            {"label": "Progress complete", "value": f"{_decimal(values.get('average_complete')).quantize(Decimal('0.01'))}%", "detail": "Average physical completion"},
            {"label": "Schedule health", "value": f"{project['health_score']}%", "detail": project["health_band"], "tone": project["health_band"].lower()},
            {"label": "Average total float", "value": f"{_decimal(values.get('average_float')).quantize(Decimal('0.01'))} days", "detail": "Current schedule"},
            {"label": "Critical activities", "value": _number(project.get("critical_task_count")), "detail": "Configured critical scope"},
            {"label": "Late milestones", "value": _number(values.get("late_milestones")), "detail": "Incomplete beyond target finish", "tone": "poor"},
            {"label": "Negative float", "value": _number(values.get("negative_float_activities")), "detail": "Activities below zero days", "tone": "poor"},
        ],
    }


def fetch_resource_overview(filters: ScheduleQualityValidationFilters) -> dict[str, object]:
    target = build_schedule_quality_target()
    where_sql, parameters = _where(filters)
    rows = fetch_rows(
        target,
        PROJECT_REPORTING_CTE
        + f"""
        SELECT TOP (100)
            COALESCE(resource.rsrc_name, resource.rsrc_short_name, 'Unspecified resource') AS resource_name,
            COUNT(DISTINCT assignment.task_id) AS activity_count,
            COUNT(DISTINCT assignment.proj_id) AS project_count,
            SUM(CONVERT(decimal(18,2), ISNULL(assignment.target_qty, 0))) AS planned_units,
            SUM(CONVERT(decimal(18,2), ISNULL(assignment.act_reg_qty, 0) + ISNULL(assignment.act_ot_qty, 0))) AS actual_units,
            SUM(CONVERT(decimal(18,2), ISNULL(assignment.remain_qty, 0))) AS remaining_units
        FROM dbo.TASKRSRC AS assignment
        JOIN project_dimensions AS project ON project.proj_id = assignment.proj_id
        LEFT JOIN dbo.RSRC AS resource ON resource.rsrc_id = assignment.rsrc_id
        {where_sql}
        GROUP BY COALESCE(resource.rsrc_name, resource.rsrc_short_name, 'Unspecified resource')
        ORDER BY remaining_units DESC, resource_name;
        """,
        parameters=parameters,
        database=target.sql_database,
    )
    return {
        "rows": rows,
        "kpis": [
            {"label": "Resources", "value": len(rows), "detail": "Named resources in the selected schedules"},
            {"label": "Assigned activities", "value": sum(_number(row.get("activity_count")) for row in rows), "detail": "Distinct activity assignments by resource"},
            {"label": "Planned units", "value": f"{sum((_decimal(row.get('planned_units')) for row in rows), Decimal('0')):,.2f}", "detail": "P6 target quantity"},
            {"label": "Remaining units", "value": f"{sum((_decimal(row.get('remaining_units')) for row in rows), Decimal('0')):,.2f}", "detail": "P6 remaining quantity"},
        ],
    }
