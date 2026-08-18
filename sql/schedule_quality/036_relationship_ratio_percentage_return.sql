/*
    Return Relationship Ratio as a raw decimal for BI Percentage formatting.
    The materialised value remains 12.84; this view returns 0.1284, which
    renders as 12.84% when the consumer formats the field as Percentage.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]', N'U') IS NULL
    THROW 51860, 'Required project-metrics result table is missing.', 1;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_ProjectMetrics]
AS
SELECT
    proj_id,
    project_name,
    updated_date,
    activity_count,
    dcma_activity_count,
    relationship_count,
    /* 0.1284 is deliberately returned for Percentage-formatted consumers. */
    CAST(relationship_ratio / 100.0 AS decimal(18,4)) AS relationship_ratio,
    missing_predecessor_count,
    missing_successor_count,
    open_start_count,
    open_finish_count,
    lead_count,
    lag_count,
    non_fs_count,
    excessive_ss_lag_count,
    excessive_ff_lag_count,
    high_float_count,
    negative_float_count,
    high_duration_count,
    constraint_count,
    invalid_date_count,
    in_progress_error_count,
    riding_progress_date_count,
    critical_task_count,
    near_critical_task_count,
    out_of_sequence_count,
    logical_loop_count,
    config_version_id
FROM [powerbitables].[xertoolkit_result_project_metrics];
GO
