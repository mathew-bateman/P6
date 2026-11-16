-- Run after fixtures.sql and 001_create_views.sql in an isolated test DB.
-- Later checks intentionally introduce new raw data after the views exist.
DO $$ BEGIN
    IF current_database() NOT LIKE 'coding_check_test_%' THEN
        RAISE EXCEPTION 'Synthetic assertions require a coding_check_test_* database';
    END IF;
END $$;
CREATE FUNCTION pg_temp.check_true(actual boolean, label text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF actual IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'Coding Check regression failed: %', label;
    END IF;
END;
$$;

CREATE FUNCTION pg_temp.check_p6_22_scope()
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    view_name text;
    problems bigint;
BEGIN
    -- Source identifiers remain internal so scope can still be checked directly.
    SELECT count(*) INTO problems FROM coding_check.projects
    WHERE _source_system IS DISTINCT FROM 'p6_22';
    PERFORM pg_temp.check_true(problems = 0, 'internal project scope is P6 22 only');
    SELECT count(*) INTO problems FROM coding_check.code_types
    WHERE _source_system IS DISTINCT FROM 'p6_22';
    PERFORM pg_temp.check_true(problems = 0, 'internal code dictionaries are P6 22 only');

    FOREACH view_name IN ARRAY ARRAY[
        'coding_check_activity_code_counts', 'coding_check_project_stage',
        'coding_check_discipline', 'coding_check_design_key',
        'coding_check_milestone_type', 'coding_check_responsible_company',
        'coding_check_path_counts', 'coding_check_path_summary'
    ] LOOP
        SELECT count(*) INTO problems FROM information_schema.columns
        WHERE table_schema = 'reporting' AND table_name = view_name
          AND column_name IN ('_source_system', '_source_database', '_project_key',
          'project_code_conflict_count', '_synced_at', '_batch_id');
        PERFORM pg_temp.check_true(problems = 0, view_name || ': removed public metadata columns are hidden');
        SELECT count(*) INTO problems FROM information_schema.columns
        WHERE table_schema = 'reporting' AND table_name = view_name
          AND column_name IN ('proj_id', 'proj_short_name', 'Project ID', 'Account',
            'Lead Planner', 'Period', 'activity_count', '_snapshot_at');
        PERFORM pg_temp.check_true(problems = 8, view_name || ': public identifiers, filters, counts and capture timestamp remain');
        EXECUTE format('SELECT count(*) FROM reporting.%I WHERE proj_id NOT IN (100,105)',
                       view_name) INTO problems;
        PERFORM pg_temp.check_true(problems = 0, view_name || ': eligible P6 22 programmes only');
        PERFORM pg_temp.check_true(
            has_table_privilege('p6_reporting_reader', 'reporting.' || view_name, 'SELECT')
            AND has_table_privilege('p6_reporting_external', 'reporting.' || view_name, 'SELECT'),
            view_name || ': internal and external readers retain access');
    END LOOP;
END;
$$;
SELECT pg_temp.check_p6_22_scope();
SELECT pg_temp.check_true(
    EXISTS (SELECT 1 FROM reporting.project WHERE _source_system = 'p6_23')
    AND EXISTS (SELECT 1 FROM reporting.task WHERE _source_system = 'p6_23'),
    'P6 23 collision fixtures remain present while coding views exclude that source');

-- Every code dimension reconciles independently to its activity population.
DO $$
DECLARE
    view_name text;
    problems bigint;
BEGIN
    FOREACH view_name IN ARRAY ARRAY[
        'coding_check_project_stage', 'coding_check_discipline',
        'coding_check_design_key', 'coding_check_milestone_type',
        'coding_check_responsible_company'
    ] LOOP
        EXECUTE format($query$
            WITH observed AS (
                SELECT proj_id, sum(activity_count) AS counted,
                       min(project_activity_count) AS smallest_total,
                       max(project_activity_count) AS largest_total
                FROM reporting.%I GROUP BY proj_id
            ), expected(project_id, expected_count) AS (
                VALUES (100, 11), (105, 0)
            )
            SELECT count(*) FROM observed o
            FULL JOIN expected e ON e.project_id = o.proj_id
            WHERE o.counted IS DISTINCT FROM e.expected_count
               OR o.smallest_total IS DISTINCT FROM e.expected_count
               OR o.largest_total IS DISTINCT FROM e.expected_count
        $query$, view_name) INTO problems;
        PERFORM pg_temp.check_true(problems = 0, view_name || ': eligibility and activity reconciliation');
    END LOOP;
END;
$$;

SELECT pg_temp.check_true(
    (SELECT count(*) = 2 FROM reporting.coding_check_path_summary),
    'path summary contains one row per eligible P6 22 project, including empty projects');
SELECT pg_temp.check_true(
    (SELECT "Project ID" = 'External Project Alpha'
        AND proj_short_name = 'P22-PROGRAMME'
        AND "Account" = 'Network Rail'
        AND "Lead Planner" = 'Mat Planner'
        AND "Period" = 'Period 1'
     FROM reporting.coding_check_path_summary WHERE proj_id = 100),
    'project code descriptions remain distinct from the programme short name');
SELECT pg_temp.check_true(
    (SELECT activity_count = 3 AND code_value = 'Option Selection'
        AND code_raw_value = 'ES3 - Option Selection'
     FROM reporting.coding_check_project_stage
     WHERE proj_id = 100 AND actv_code_id = 101 AND bucket_status = 'coded'),
    'duplicate assignments and valid-plus-blank assignments count once; raw stage text is retained');
SELECT pg_temp.check_true(
    (SELECT activity_count = 1 AND code_value = 'Approval in Principle Design'
     FROM reporting.coding_check_project_stage
     WHERE proj_id = 100 AND actv_code_id = 102 AND bucket_status = 'coded'),
    'en dash stage prefix is removed');
SELECT pg_temp.check_true(
    (SELECT activity_count = 0 AND code_value = 'Detailed Design / Construction'
     FROM reporting.coding_check_project_stage
     WHERE proj_id = 100 AND actv_code_id = 106),
    'zero-count dictionary values and combined ES stage prefixes are supported');
SELECT pg_temp.check_true(
    (SELECT sum(activity_count) = 1 FROM reporting.coding_check_project_stage
     WHERE proj_id = 100 AND bucket_status = 'conflict'),
    'multiple distinct valid assignments have one conflict bucket');
SELECT pg_temp.check_true(
    (SELECT sum(activity_count) = 6 FROM reporting.coding_check_project_stage
     WHERE proj_id = 100 AND bucket_status = 'uncoded' AND is_uncoded),
    'absent, blank, dangling, deleted, and nonglobal assignments are uncoded');
SELECT pg_temp.check_true(
    NOT EXISTS (SELECT 1 FROM reporting.coding_check_project_stage
                WHERE actv_code_id IN (103, 104, 105)),
    'blank, deleted, and project-scoped dictionary values do not become coded rows');
SELECT pg_temp.check_true(
    (SELECT activity_count = 2 FROM reporting.coding_check_discipline
     WHERE proj_id = 100 AND actv_code_id = 201),
    'duplicate discipline assignment rows do not inflate counts');
SELECT pg_temp.check_true(
    (SELECT bool_and(NOT code_type_exists) AND sum(activity_count) = 11
     FROM reporting.coding_check_design_key WHERE proj_id = 100),
    'absent Design Key type produces uncoded counts instead of losing activities');
SELECT pg_temp.check_true(
    (SELECT count(*) = 1 AND sum(activity_count) = 0 AND bool_and(is_uncoded)
     FROM reporting.coding_check_design_key WHERE proj_id = 105),
    'empty projects retain an explicit zero uncoded scaffold');

SELECT pg_temp.check_true(
    (SELECT activity_count = 11
        AND critical_path_count = 2
        AND project_critical_count = 5
        AND negative_float_count = 2
        AND other_float_count = 1
        AND missing_float_count = 1
        AND longest_path_count = 2
        AND missing_longest_path_flag_count = 3
     FROM reporting.coding_check_path_summary WHERE proj_id = 100),
    'exclusive float boundaries, independent longest path, missing values, and deleted/retired exclusions');
SELECT pg_temp.check_true(
    NOT EXISTS (
        SELECT 1 FROM reporting.coding_check_path_summary
        WHERE activity_count <> critical_path_count + project_critical_count
             + negative_float_count + other_float_count + missing_float_count
    ),
    'exclusive float categories reconcile exactly to activity totals');
-- These writes affect synthetic fixture tables only, after view deployment.
INSERT INTO reporting.actvcode
(_source_system, actv_code_id, actv_code_type_id, short_name, actv_code_name, seq_num)
VALUES ('p6_22', 107, 10, 'ES10', 'ES10 - Future Stage', 7);
SELECT pg_temp.check_true(
    (SELECT activity_count = 0 AND code_value = 'Future Stage'
     FROM reporting.coding_check_project_stage
     WHERE proj_id = 100 AND actv_code_id = 107),
    'a new dictionary value appears without replacing SQL views');

INSERT INTO reporting.actvtype
(_source_system, actv_code_type_id, actv_code_type, actv_code_type_scope)
VALUES ('p6_22', 50, 'Design Key', 'AS_Global');
INSERT INTO reporting.actvcode
(_source_system, actv_code_id, actv_code_type_id, short_name, actv_code_name, seq_num)
VALUES ('p6_22', 501, 50, 'START', 'Design Start', 1);
INSERT INTO reporting.taskactv
(_source_system, proj_id, task_id, actv_code_type_id, actv_code_id)
VALUES ('p6_22', 100, 2, 50, 501);
SELECT pg_temp.check_true(
    (SELECT activity_count = 1 AND code_type_exists AND code_value = 'Design Start'
     FROM reporting.coding_check_design_key
     WHERE proj_id = 100 AND actv_code_id = 501),
    'a future code type and assignment become visible automatically');
SELECT pg_temp.check_true(
    (SELECT sum(activity_count) = 10 FROM reporting.coding_check_design_key
     WHERE proj_id = 100 AND is_uncoded),
    'a future Design Key assignment moves one activity out of uncoded');

INSERT INTO reporting.actvtype
(_source_system, actv_code_type_id, actv_code_type, actv_code_type_scope)
VALUES ('p6_23', 22, 'Discipline', 'AS_Global');
INSERT INTO reporting.actvcode
(_source_system, actv_code_id, actv_code_type_id, short_name, actv_code_name, seq_num)
VALUES ('p6_23', 202, 22, 'TEL', 'Source 23 Telecoms', 1);
INSERT INTO reporting.taskactv
(_source_system, proj_id, task_id, actv_code_type_id, actv_code_id)
VALUES ('p6_23', 100, 2, 22, 202);
SELECT pg_temp.check_p6_22_scope();
SELECT pg_temp.check_true(
    (SELECT activity_count = 2 AND code_value = 'Signalling'
     FROM reporting.coding_check_discipline
     WHERE proj_id = 100 AND actv_code_id = 201),
    'new exact-name P6 23 codes and colliding IDs do not change P6 22 coding counts');

INSERT INTO reporting.actvcode
(_source_system, actv_code_id, actv_code_type_id, short_name, actv_code_name, seq_num)
VALUES ('p6_22', 108, 10, E'\t\r', E'\n\t', 8),
       ('p6_22', 109, 10, 'ES3', 'ES3 -', 9);
INSERT INTO reporting.taskactv
(_source_system, proj_id, task_id, actv_code_type_id, actv_code_id)
VALUES ('p6_22', 100, 3, 10, 108);
SELECT pg_temp.check_true(
    (SELECT sum(activity_count) = 6 FROM reporting.coding_check_project_stage
     WHERE proj_id = 100 AND is_uncoded)
    AND NOT EXISTS (SELECT 1 FROM reporting.coding_check_project_stage WHERE actv_code_id = 108),
    'all-whitespace values remain uncoded and do not create chart categories');
SELECT pg_temp.check_true(
    (SELECT code_value = 'ES3 -' FROM reporting.coding_check_project_stage
     WHERE proj_id = 100 AND actv_code_id = 109),
    'a prefix-only stage preserves its nonempty raw label');

UPDATE reporting.task SET driving_path_flag = E'\t\n'
WHERE _source_system = 'p6_22' AND proj_id = 100 AND task_id = 3;
SELECT pg_temp.check_true(
    (SELECT missing_longest_path_flag_count = 3 AND invalid_longest_path_flag_count = 0
     FROM reporting.coding_check_path_summary WHERE proj_id = 100),
    'whitespace-only path flags are missing, rather than invalid or false');
UPDATE reporting.task SET driving_path_flag = E'\tY\n'
WHERE _source_system = 'p6_22' AND proj_id = 100 AND task_id = 1;
SELECT pg_temp.check_true(
    (SELECT longest_path_count = 2 AND invalid_longest_path_flag_count = 0
     FROM reporting.coding_check_path_summary WHERE proj_id = 100),
    'whitespace-padded Y flags are consistently counted as valid longest-path flags');

-- The stable publication pointer changes without replacing coding-check views.
CREATE SCHEMA fixture_generation_b;
CREATE TABLE fixture_generation_b.actvcode AS SELECT * FROM reporting.actvcode;
INSERT INTO fixture_generation_b.actvcode
(_source_system, actv_code_id, actv_code_type_id, short_name, actv_code_name, seq_num)
VALUES ('p6_22', 110, 10, 'ES11', 'ES11 - Next Publication', 10);
CREATE OR REPLACE VIEW reporting.actvcode AS SELECT * FROM fixture_generation_b.actvcode;
DROP SCHEMA fixture_generation_a CASCADE;
SELECT pg_temp.check_true(
    (SELECT code_value = 'Next Publication' AND activity_count = 0
     FROM reporting.coding_check_project_stage
     WHERE proj_id = 100 AND actv_code_id = 110),
    'stable-view publication and old-generation pruning preserve coding-check views');

-- Conflicting filter classifications are visible and cannot multiply totals.
INSERT INTO reporting.pcatval
(_source_system, proj_catg_id, proj_catg_type_id, proj_catg_short_name, proj_catg_name)
VALUES ('p6_22', 4002, 4, 'OTHER', 'Other Account');
INSERT INTO reporting.projpcat (_source_system, proj_id, proj_catg_type_id, proj_catg_id)
VALUES ('p6_22', 100, 4, 4002);
SELECT pg_temp.check_true(
    (SELECT "Account" IS NULL AND activity_count = 11
     FROM reporting.coding_check_path_summary WHERE proj_id = 100)
    AND (SELECT project_code_conflict_count = 1 FROM coding_check.projects
         WHERE _source_system = 'p6_22' AND proj_id = 100),
    'ambiguous filter values stay NULL; the conflict diagnostic remains internal');

SET ROLE p6_reporting_reader;
SELECT count(*) AS readable_code_rows FROM reporting.coding_check_activity_code_counts;
SELECT count(*) AS readable_path_rows FROM reporting.coding_check_path_counts;
RESET ROLE;
SELECT pg_temp.check_p6_22_scope();

SELECT 'Coding Check regression assertions passed' AS result;
