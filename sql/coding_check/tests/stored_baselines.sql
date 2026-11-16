-- Run after fixtures.sql, 001_create_views.sql and assertions.sql with psql.
-- Applies 006 twice and proves existing rows, columns, ACLs and dependencies survive.
DO $$ BEGIN
  IF current_database() NOT LIKE 'coding_check_test_%' THEN
    RAISE EXCEPTION 'Synthetic assertions require a coding_check_test_* database';
  END IF;
END $$;

CREATE TEMP TABLE baseline_view_before (
  view_name text PRIMARY KEY, object_id oid, owner_id oid, privileges text,
  columns text[], rows jsonb
);
DO $$
DECLARE view_name text; contents jsonb;
BEGIN
  FOREACH view_name IN ARRAY ARRAY[
    'coding_check_activity_code_counts', 'coding_check_project_stage',
    'coding_check_discipline', 'coding_check_design_key',
    'coding_check_milestone_type', 'coding_check_responsible_company',
    'coding_check_path_summary', 'coding_check_path_counts'
  ] LOOP
    EXECUTE format('SELECT jsonb_agg(row ORDER BY row::text) FROM '
      || '(SELECT to_jsonb(v) - ARRAY[''is_stored_baseline'', ''original_proj_id'', '
      || '''original_proj_short_name''] AS row FROM reporting.%I v) s', view_name)
      INTO contents;
    INSERT INTO baseline_view_before
    SELECT view_name, c.oid, c.relowner, c.relacl::text,
      (SELECT array_agg(a.attname::text ORDER BY a.attnum) FROM pg_attribute a
       WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
         AND a.attname NOT IN ('is_stored_baseline', 'original_proj_id', 'original_proj_short_name')),
      contents
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'reporting' AND c.relname = view_name;
  END LOOP;
END $$;
CREATE TEMP VIEW baseline_existing_dependency AS
SELECT proj_id, activity_count FROM reporting.coding_check_path_summary;

\ir ../006_mark_stored_baselines.sql
\ir ../006_mark_stored_baselines.sql

-- Every original fixture project starts as an ordinary P6 project.
DO $$
DECLARE before_row record; problems bigint;
BEGIN
  FOR before_row IN SELECT * FROM baseline_view_before LOOP
    EXECUTE format('SELECT count(*) FROM reporting.%I WHERE is_stored_baseline '
      || 'OR original_proj_id IS NOT NULL OR original_proj_short_name IS NOT NULL',
      before_row.view_name) INTO problems;
    IF problems <> 0 THEN RAISE EXCEPTION 'Ordinary project metadata wrong in %', before_row.view_name; END IF;
  END LOOP;
END $$;

-- Parent has no eligible submission codes; baseline still stays in the results.
-- The P6 23 collision must never supply the parent name or multiply rows.
INSERT INTO reporting.project (_source_system, proj_id, proj_short_name, project_flag)
VALUES ('p6_22', 200, 'P22-ORIGINAL', 'Y'), ('p6_23', 200, 'WRONG-SOURCE-ORIGINAL', 'Y');
UPDATE reporting.project SET orig_proj_id = 200 WHERE _source_system = 'p6_22' AND proj_id = 100;
UPDATE reporting.project SET orig_proj_id = 999 WHERE _source_system = 'p6_22' AND proj_id = 105;

DO $$
DECLARE before_row record; problems bigint; contents jsonb; actual_columns text[];
BEGIN
  FOR before_row IN SELECT * FROM baseline_view_before LOOP
    EXECUTE format('SELECT count(*) FROM reporting.%I WHERE '
      || '(proj_id = 100 AND (is_stored_baseline IS DISTINCT FROM TRUE '
      || 'OR original_proj_id IS DISTINCT FROM 200 '
      || 'OR original_proj_short_name IS DISTINCT FROM ''P22-ORIGINAL'')) '
      || 'OR (proj_id = 105 AND (is_stored_baseline IS DISTINCT FROM TRUE '
      || 'OR original_proj_id IS DISTINCT FROM 999 OR original_proj_short_name IS NOT NULL))',
      before_row.view_name) INTO problems;
    IF problems <> 0 THEN RAISE EXCEPTION 'Stored baseline metadata wrong in %', before_row.view_name; END IF;

    EXECUTE format('SELECT jsonb_agg(row ORDER BY row::text) FROM '
      || '(SELECT to_jsonb(v) - ARRAY[''is_stored_baseline'', ''original_proj_id'', '
      || '''original_proj_short_name''] AS row FROM reporting.%I v) s', before_row.view_name)
      INTO contents;
    IF contents IS DISTINCT FROM before_row.rows THEN
      RAISE EXCEPTION 'Existing Coding Check rows or counts changed in %', before_row.view_name;
    END IF;
    SELECT array_agg(a.attname::text ORDER BY a.attnum) INTO actual_columns
    FROM pg_attribute a WHERE a.attrelid = before_row.object_id AND a.attnum > 0 AND NOT a.attisdropped;
    IF actual_columns IS DISTINCT FROM before_row.columns || ARRAY[
      'is_stored_baseline', 'original_proj_id', 'original_proj_short_name'] THEN
      RAISE EXCEPTION 'Baseline columns were not appended in %', before_row.view_name;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'reporting' AND c.relname = before_row.view_name
        AND c.oid = before_row.object_id AND c.relowner = before_row.owner_id
        AND c.relacl::text IS NOT DISTINCT FROM before_row.privileges) THEN
      RAISE EXCEPTION 'Object identity, ownership or grants changed in %', before_row.view_name;
    END IF;
  END LOOP;
END $$;

-- A changed baseline link is reflected without reinstalling views.
UPDATE reporting.project SET orig_proj_id = NULL WHERE _source_system = 'p6_22' AND proj_id = 100;
DO $$
DECLARE before_row record; problems bigint;
BEGIN
  FOR before_row IN SELECT * FROM baseline_view_before LOOP
    EXECUTE format('SELECT count(*) FROM reporting.%I WHERE proj_id = 100 AND '
      || '(is_stored_baseline IS DISTINCT FROM FALSE OR original_proj_id IS NOT NULL '
      || 'OR original_proj_short_name IS NOT NULL)', before_row.view_name) INTO problems;
    IF problems <> 0 THEN RAISE EXCEPTION 'Changed baseline link is stale in %', before_row.view_name; END IF;
  END LOOP;
END $$;
UPDATE reporting.project SET orig_proj_id = 200 WHERE _source_system = 'p6_22' AND proj_id = 100;

SET ROLE p6_reporting_external;
SELECT proj_id, is_stored_baseline, original_proj_id, original_proj_short_name
FROM reporting.coding_check_path_summary ORDER BY proj_id;
RESET ROLE;
SELECT * FROM baseline_existing_dependency ORDER BY proj_id;
SELECT 'Stored baseline regression assertions passed' AS result;
