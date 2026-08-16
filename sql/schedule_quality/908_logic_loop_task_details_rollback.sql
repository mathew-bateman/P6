/* Restore the original four-column Power BI logic-loop view. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51810, 'This rollback must be run against P62212_1.', 1;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_LogicLoops]
AS
SELECT
    proj_id,
    task_id,
    loop_path,
    loop_length
FROM [powerbitables].[xertoolkit_result_logic_loop_tasks];
GO

SELECT
    COL_LENGTH(N'[powerbitables].[xertoolkit_vw_PBI_LogicLoops]', N'is_logical_loop') AS boolean_identifier_length,
    COL_LENGTH(N'[powerbitables].[xertoolkit_vw_PBI_LogicLoops]', N'logical_loop_status') AS text_identifier_length;
