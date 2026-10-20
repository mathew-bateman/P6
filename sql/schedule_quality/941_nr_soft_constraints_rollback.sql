/* Restore the exact function captured before 041, then rebuild results. */
SET XACT_ABORT ON;
IF DB_NAME()<>N'P62212_1' THROW 51941,'Run against P62212_1.',1;
DECLARE @definition nvarchar(max)=(SELECT definition FROM powerbitables.xertoolkit_nr_soft_constraints_backup
    WHERE object_name='xertoolkit_fn_soft_constraints');
IF @definition IS NULL THROW 51942,'Pre-041 function snapshot missing.',1;
SET @definition=STUFF(@definition,1,CHARINDEX('FUNCTION',UPPER(@definition))+7,N'ALTER FUNCTION');
EXEC sys.sp_executesql @definition;
