from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings

from backups.services.mssql import (
    execute_statement,
    fetch_result_sets,
    fetch_rows,
    sql_string,
)


SCHEDULE_QUALITY_SCHEMA = "powerbitables"
SCHEDULE_QUALITY_PROFILE_CODE = "default"
SETTINGS_HASH_PATTERN = re.compile(r"^[0-9A-Fa-f]{64}$")
LONDON_TIMEZONE = ZoneInfo("Europe/London")


class ScheduleQualitySettingsConflict(RuntimeError):
    """The draft changed after the caller read its optimistic-lock token."""


@dataclass(frozen=True)
class ScheduleQualityCheckScope:
    check_code: str
    display_name: str
    sort_order: int
    is_enabled: bool
    include_loe: bool | None
    include_wbs_summary: bool | None
    include_milestones: bool | None
    exclude_complete: bool | None
    limit_type: str = "Percent"
    green_limit: Decimal = Decimal("0")
    amber_limit: Decimal = Decimal("0")
    green_points: int = 0
    amber_points: int = 0
    records_metric: str = ""
    qualifying_metric: str = ""


@dataclass(frozen=True)
class ScheduleQualityOption:
    option_code: str
    display_name: str
    data_type: str
    bit_value: bool | None
    numeric_value: Decimal | int | float | None
    text_value: str | None
    unit_code: str | None
    sort_order: int


@dataclass(frozen=True)
class ScheduleQualityConstraintType:
    constraint_type_code: str
    display_name: str
    is_checked: bool
    sort_order: int


@dataclass(frozen=True)
class ScheduleQualityDetailField:
    detail_field_id: int
    check_code: str
    source_category: str
    source_identifier: str
    display_label: str
    display_format: str
    sort_order: int


@dataclass(frozen=True)
class ScheduleQualitySettingsSnapshot:
    profile_code: str
    profile_name: str
    active_config_version_id: int | None
    config_version_id: int
    version_number: int
    state: str
    settings_hash: str
    based_on_config_version_id: int | None
    change_note: str
    created_at: object
    created_by: str
    checks: tuple[ScheduleQualityCheckScope, ...]
    options: tuple[ScheduleQualityOption, ...]
    constraint_types: tuple[ScheduleQualityConstraintType, ...]
    detail_fields: tuple[ScheduleQualityDetailField, ...] = ()


@dataclass(frozen=True)
class ScheduleQualityTarget:
    sql_host: str
    sql_port: int
    sql_database: str
    sql_username: str
    sql_password_env: str
    odbc_driver: str
    encrypt_connection: bool
    trust_server_certificate: bool

    @property
    def sql_password(self) -> str:
        return self.sql_password_env.strip()


def build_schedule_quality_target() -> ScheduleQualityTarget:
    return ScheduleQualityTarget(
        sql_host=settings.P6_SCHEDULE_QUALITY_SQL_HOST,
        sql_port=settings.P6_SCHEDULE_QUALITY_SQL_PORT,
        sql_database=settings.P6_SCHEDULE_QUALITY_SQL_DATABASE,
        sql_username=settings.P6_SCHEDULE_QUALITY_SQL_USERNAME,
        sql_password_env=settings.P6_SCHEDULE_QUALITY_SQL_PASSWORD,
        odbc_driver=settings.P6_SCHEDULE_QUALITY_SQL_DRIVER,
        encrypt_connection=settings.P6_SCHEDULE_QUALITY_SQL_ENCRYPT,
        trust_server_certificate=settings.P6_SCHEDULE_QUALITY_SQL_TRUST_CERT,
    )


def get_schedule_quality_profile_code() -> str:
    return str(
        getattr(
            settings,
            "P6_SCHEDULE_QUALITY_PROFILE_CODE",
            SCHEDULE_QUALITY_PROFILE_CODE,
        )
    ).strip() or SCHEDULE_QUALITY_PROFILE_CODE


def get_or_create_schedule_quality_draft(
    *,
    changed_by: str,
    profile_code: str | None = None,
) -> int:
    target = build_schedule_quality_target()
    rows = fetch_rows(
        target,
        f"""
        DECLARE @config_version_id bigint;
        EXEC [{SCHEDULE_QUALITY_SCHEMA}].[xertoolkit_get_or_create_schedule_quality_draft]
            @profile_code = ?,
            @changed_by = ?,
            @config_version_id = @config_version_id OUTPUT;
        SELECT @config_version_id AS config_version_id;
        """,
        parameters=(profile_code or get_schedule_quality_profile_code(), changed_by),
        database=target.sql_database,
        transactional=False,
    )
    if not rows or not rows[0].get("config_version_id"):
        raise RuntimeError("SQL Server did not return a schedule quality draft version.")
    return int(rows[0]["config_version_id"])


def _nullable_bool(value: object) -> bool | None:
    return None if value is None else bool(value)


def _validated_settings_hash(value: object) -> str:
    settings_hash = str(value or "").strip()
    if not SETTINGS_HASH_PATTERN.fullmatch(settings_hash):
        raise ValueError("settings_hash must be a 64-character hexadecimal value.")
    return settings_hash.upper()


def _raise_settings_write_error(error: Exception) -> None:
    message = str(error).lower()
    conflict_markers = (
        "the draft changed after it was loaded",
        "not a publishable default-profile draft",
        "only a draft configuration",
    )
    if any(marker in message for marker in conflict_markers):
        raise ScheduleQualitySettingsConflict(
            "The draft changed after it was loaded. Reload it before continuing."
        ) from error
    raise error


def fetch_schedule_quality_settings(
    *,
    config_version_id: int,
) -> ScheduleQualitySettingsSnapshot:
    target = build_schedule_quality_target()
    result_sets = fetch_result_sets(
        target,
        f"""
        SELECT
            profile.profile_code,
            profile.profile_name,
            profile.active_config_version_id,
            config_version.config_version_id,
            config_version.version_number,
            config_version.state,
            config_version.settings_hash,
            config_version.based_on_config_version_id,
            config_version.change_note,
            config_version.created_at,
            config_version.created_by
        FROM [{SCHEDULE_QUALITY_SCHEMA}].[xertoolkit_schedule_quality_config_version] AS config_version
        INNER JOIN [{SCHEDULE_QUALITY_SCHEMA}].[xertoolkit_schedule_quality_profile] AS profile
            ON profile.profile_id = config_version.profile_id
        WHERE config_version.config_version_id = ?;

        SELECT
            check_code,
            display_name,
            sort_order,
            is_enabled,
            include_loe,
            include_wbs_summary,
            include_milestones,
            exclude_complete,
            limit_type,
            green_limit,
            amber_limit,
            green_points,
            amber_points,
            records_metric,
            qualifying_metric
        FROM [{SCHEDULE_QUALITY_SCHEMA}].[xertoolkit_schedule_quality_check_scope]
        WHERE config_version_id = ?
        ORDER BY sort_order, check_code;

        SELECT
            option_code,
            display_name,
            data_type,
            bit_value,
            numeric_value,
            text_value,
            unit_code,
            sort_order
        FROM [{SCHEDULE_QUALITY_SCHEMA}].[xertoolkit_schedule_quality_option]
        WHERE config_version_id = ?
        ORDER BY sort_order, option_code;

        SELECT
            constraint_type_code,
            display_name,
            is_checked,
            sort_order
        FROM [{SCHEDULE_QUALITY_SCHEMA}].[xertoolkit_schedule_quality_constraint_type]
        WHERE config_version_id = ?
        ORDER BY sort_order, constraint_type_code;

        SELECT
            detail_field_id,
            check_code,
            source_category,
            source_identifier,
            display_label,
            display_format,
            sort_order
        FROM [{SCHEDULE_QUALITY_SCHEMA}].[xertoolkit_schedule_quality_detail_field]
        WHERE config_version_id = ?
        ORDER BY check_code, sort_order, detail_field_id;
        """,
        parameters=(config_version_id,) * 5,
        database=target.sql_database,
    )
    if len(result_sets) < 4 or not result_sets[0]:
        raise LookupError(f"Schedule quality configuration {config_version_id} was not found.")

    header = result_sets[0][0]
    checks = tuple(
        ScheduleQualityCheckScope(
            check_code=str(row["check_code"]),
            display_name=str(row["display_name"]),
            sort_order=int(row["sort_order"]),
            is_enabled=bool(row["is_enabled"]),
            include_loe=_nullable_bool(row.get("include_loe")),
            include_wbs_summary=_nullable_bool(row.get("include_wbs_summary")),
            include_milestones=_nullable_bool(row.get("include_milestones")),
            exclude_complete=_nullable_bool(row.get("exclude_complete")),
            limit_type=str(row.get("limit_type") or "Percent"),
            green_limit=Decimal(str(row.get("green_limit") or "0")),
            amber_limit=Decimal(str(row.get("amber_limit") or "0")),
            green_points=int(row.get("green_points") or 0),
            amber_points=int(row.get("amber_points") or 0),
            records_metric=str(row.get("records_metric") or ""),
            qualifying_metric=str(row.get("qualifying_metric") or ""),
        )
        for row in result_sets[1]
    )
    options = tuple(
        ScheduleQualityOption(
            option_code=str(row["option_code"]),
            display_name=str(row["display_name"]),
            data_type=str(row["data_type"]),
            bit_value=_nullable_bool(row.get("bit_value")),
            numeric_value=row.get("numeric_value"),
            text_value=None if row.get("text_value") is None else str(row["text_value"]),
            unit_code=None if row.get("unit_code") is None else str(row["unit_code"]),
            sort_order=int(row["sort_order"]),
        )
        for row in result_sets[2]
    )
    constraint_types = tuple(
        ScheduleQualityConstraintType(
            constraint_type_code=str(row["constraint_type_code"]),
            display_name=str(row["display_name"]),
            is_checked=bool(row["is_checked"]),
            sort_order=int(row["sort_order"]),
        )
        for row in result_sets[3]
    )
    detail_fields = tuple(
        ScheduleQualityDetailField(
            detail_field_id=int(row["detail_field_id"]),
            check_code=str(row["check_code"]),
            source_category=str(row["source_category"]),
            source_identifier=str(row["source_identifier"]),
            display_label=str(row["display_label"]),
            display_format=str(row.get("display_format") or "native"),
            sort_order=int(row["sort_order"]),
        )
        for row in (result_sets[4] if len(result_sets) > 4 else [])
    )
    return ScheduleQualitySettingsSnapshot(
        profile_code=str(header["profile_code"]),
        profile_name=str(header["profile_name"]),
        active_config_version_id=(
            None
            if header.get("active_config_version_id") is None
            else int(header["active_config_version_id"])
        ),
        config_version_id=int(header["config_version_id"]),
        version_number=int(header["version_number"]),
        state=str(header["state"]),
        settings_hash=_validated_settings_hash(header.get("settings_hash")),
        based_on_config_version_id=(
            None
            if header.get("based_on_config_version_id") is None
            else int(header["based_on_config_version_id"])
        ),
        change_note=str(header.get("change_note") or ""),
        created_at=header.get("created_at"),
        created_by=str(header.get("created_by") or ""),
        checks=checks,
        options=options,
        constraint_types=constraint_types,
        detail_fields=detail_fields,
    )


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def save_schedule_quality_draft(
    *,
    config_version_id: int,
    payload: dict[str, Any],
    expected_settings_hash: str,
    changed_by: str,
    change_note: str = "",
) -> str:
    target = build_schedule_quality_target()
    expected_settings_hash = _validated_settings_hash(expected_settings_hash)
    settings_json = json.dumps(
        payload,
        default=_json_default,
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        rows = fetch_rows(
            target,
            f"""
            EXEC [{SCHEDULE_QUALITY_SCHEMA}].[xertoolkit_save_schedule_quality_draft]
                @config_version_id = ?,
                @settings_json = ?,
                @expected_settings_hash = ?,
                @changed_by = ?,
                @change_note = ?;
            """,
            parameters=(
                config_version_id,
                settings_json,
                expected_settings_hash,
                changed_by,
                change_note or None,
            ),
            database=target.sql_database,
            transactional=False,
        )
    except Exception as error:
        _raise_settings_write_error(error)
    if not rows:
        raise RuntimeError("SQL Server did not return the saved settings version.")
    return _validated_settings_hash(rows[0].get("settings_hash"))


def publish_schedule_quality_config(
    *,
    config_version_id: int,
    expected_settings_hash: str,
    published_by: str,
    trigger_type: str = "publish",
) -> None:
    target = build_schedule_quality_target()
    expected_settings_hash = _validated_settings_hash(expected_settings_hash)
    try:
        execute_statement(
            target,
            f"""
            EXEC [{SCHEDULE_QUALITY_SCHEMA}].[xertoolkit_publish_schedule_quality_config]
                @config_version_id = ?,
                @expected_settings_hash = ?,
                @published_by = ?,
                @trigger_type = ?;
            """,
            parameters=(
                config_version_id,
                expected_settings_hash,
                published_by,
                trigger_type,
            ),
            database=target.sql_database,
            transactional=False,
        )
    except Exception as error:
        _raise_settings_write_error(error)


def build_refresh_schedule_quality_sql(
    *,
    proj_id: int | None = None,
    config_version_id: int | None = None,
    expected_settings_hash: str | None = None,
    trigger: str = "scheduled",
) -> str:
    trigger_value = sql_string(trigger.strip().lower() or "scheduled")
    parameters = [f"@trigger_type = {trigger_value}"]
    if proj_id is not None:
        parameters.insert(0, f"@proj_id = {int(proj_id)}")
    if config_version_id is not None:
        insert_at = 1 if proj_id is not None else 0
        parameters.insert(insert_at, f"@config_version_id = {int(config_version_id)}")
    if expected_settings_hash is not None:
        expected_hash_value = sql_string(_validated_settings_hash(expected_settings_hash))
        insert_at = int(proj_id is not None) + int(config_version_id is not None)
        parameters.insert(insert_at, f"@expected_settings_hash = {expected_hash_value}")
    return (
        "EXEC [powerbitables].[xertoolkit_refresh_all_schedule_quality] "
        + ", ".join(parameters)
        + ";"
    )


def refresh_schedule_quality(
    *,
    proj_id: int | None = None,
    config_version_id: int | None = None,
    expected_settings_hash: str | None = None,
    trigger: str = "scheduled",
) -> str:
    target = build_schedule_quality_target()
    sql = build_refresh_schedule_quality_sql(
        proj_id=proj_id,
        config_version_id=config_version_id,
        expected_settings_hash=expected_settings_hash,
        trigger=trigger,
    )
    execute_statement(target, sql, database=target.sql_database)
    project_label = "all projects" if proj_id is None else f"proj_id={proj_id}"
    return f"Schedule quality refresh completed for {project_label}."


def format_refresh_duration(started_at: object, completed_at: object) -> str:
    if not started_at or not completed_at:
        return "-"

    duration = completed_at - started_at
    total_seconds = max(0, int(duration.total_seconds()))
    minutes, seconds = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def as_london_time(value: object) -> object:
    """Convert SQL Server's UTC, naïve refresh timestamps for display."""
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(LONDON_TIMEZONE)


def fetch_schedule_quality_refresh_history(
    *,
    limit: int = 10,
    offset: int = 0,
    status: str = "",
) -> list[dict[str, object]]:
    target = build_schedule_quality_target()
    safe_limit = max(1, min(int(limit), 50))
    safe_offset = max(0, int(offset))
    status_filter = status.strip().lower()
    if status_filter not in {"success", "failed", "running"}:
        status_filter = ""
    status_clause = f"WHERE status = {sql_string(status_filter)}" if status_filter else ""
    rows = fetch_rows(
        target,
        f"""
        SELECT
            check_run_id,
            requested_proj_id,
            config_version_id,
            started_at,
            completed_at,
            status,
            processed_project_count,
            logic_loop_task_count,
            ISNULL(trigger_type, 'scheduled') AS trigger_type,
            error_message
        FROM [powerbitables].[xertoolkit_refresh_run_history]
        {status_clause}
        ORDER BY check_run_id DESC
        OFFSET {safe_offset} ROWS FETCH NEXT {safe_limit} ROWS ONLY;
        """,
        database=target.sql_database,
    )
    for row in rows:
        row["started_at"] = as_london_time(row.get("started_at"))
        row["completed_at"] = as_london_time(row.get("completed_at"))
        row["duration_display"] = format_refresh_duration(row.get("started_at"), row.get("completed_at"))
    return rows


def count_schedule_quality_refresh_history(*, status: str = "") -> int:
    target = build_schedule_quality_target()
    status_filter = status.strip().lower()
    if status_filter not in {"success", "failed", "running"}:
        status_filter = ""
    status_clause = f"WHERE status = {sql_string(status_filter)}" if status_filter else ""
    rows = fetch_rows(
        target,
        f"""
        SELECT COUNT(*) AS run_count
        FROM [powerbitables].[xertoolkit_refresh_run_history]
        {status_clause};
        """,
        database=target.sql_database,
    )
    if not rows:
        return 0
    return int(rows[0].get("run_count") or 0)
