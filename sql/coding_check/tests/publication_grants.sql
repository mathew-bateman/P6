-- Run in an isolated test database after each view-creating migration.
DO $$
DECLARE view_name text;
BEGIN
  IF current_database() NOT LIKE 'coding_check_test_%' THEN
    RAISE EXCEPTION 'Synthetic assertions require a coding_check_test_* database';
  END IF;
  FOREACH view_name IN ARRAY ARRAY[
    'coding_check_activity_code_counts', 'coding_check_project_stage',
    'coding_check_discipline', 'coding_check_design_key',
    'coding_check_milestone_type', 'coding_check_responsible_company',
    'coding_check_path_summary', 'coding_check_path_counts'
  ] LOOP
    IF NOT has_table_privilege('p6_reporting_sync', 'reporting.' || view_name,
                               'SELECT WITH GRANT OPTION') THEN
      RAISE EXCEPTION 'Sync cannot grant SELECT on %', view_name;
    END IF;
    IF NOT has_table_privilege('p6_reporting_reader', 'reporting.' || view_name, 'SELECT') THEN
      RAISE EXCEPTION 'Reader cannot SELECT from %', view_name;
    END IF;
  END LOOP;
END $$;

-- Exercise the publisher's actual schema-wide statement as its login role;
-- roll back to leave the fixture's access state unchanged.
BEGIN;
SET LOCAL ROLE p6_reporting_sync;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO p6_reporting_reader;
ROLLBACK;
