-- Synthetic data only. Run in a fresh, isolated PostgreSQL test database.
-- Do not run this file against the reporting database.
DO $$ BEGIN
    IF current_database() NOT LIKE 'coding_check_test_%' THEN
        RAISE EXCEPTION 'Synthetic fixtures require a coding_check_test_* database';
    END IF;
END $$;
CREATE SCHEMA reporting;

CREATE TABLE reporting.project (
    _source_system text NOT NULL,
    proj_id integer NOT NULL,
    proj_short_name text,
    orig_proj_id integer,
    project_flag text DEFAULT 'Y',
    delete_date timestamp,
    delete_session_id integer,
    _source_database text DEFAULT 'synthetic_fixture',
    _snapshot_at timestamptz DEFAULT '2026-10-29 08:00:00+00',
    _synced_at timestamptz DEFAULT '2026-10-29 08:05:00+00',
    _batch_id text DEFAULT 'coding-check-fixture',
    _project_key text
);
CREATE TABLE reporting.projwbs (
    _source_system text NOT NULL,
    proj_id integer,
    wbs_id integer,
    parent_wbs_id integer,
    wbs_name text,
    proj_node_flag text,
    delete_date timestamp,
    delete_session_id integer
);
CREATE TABLE reporting.pcattype (
    _source_system text NOT NULL,
    proj_catg_type_id integer,
    proj_catg_type text,
    delete_date timestamp,
    delete_session_id integer
);
CREATE TABLE reporting.pcatval (
    _source_system text NOT NULL,
    proj_catg_id integer,
    proj_catg_type_id integer,
    proj_catg_short_name text,
    proj_catg_name text,
    delete_date timestamp,
    delete_session_id integer
);
CREATE TABLE reporting.projpcat (
    _source_system text NOT NULL,
    proj_id integer,
    proj_catg_type_id integer,
    proj_catg_id integer,
    delete_date timestamp,
    delete_session_id integer
);
CREATE TABLE reporting.task (
    _source_system text NOT NULL,
    proj_id integer NOT NULL,
    task_id integer NOT NULL,
    task_code text,
    task_name text,
    task_type text DEFAULT 'TT_Task',
    status_code text DEFAULT 'TK_NotStart',
    total_float_hr_cnt numeric,
    driving_path_flag text,
    delete_date timestamp,
    delete_session_id integer,
    _source_database text DEFAULT 'synthetic_fixture',
    _snapshot_at timestamptz DEFAULT '2026-10-29 08:00:00+00',
    _synced_at timestamptz DEFAULT '2026-10-29 08:05:00+00',
    _batch_id text DEFAULT 'coding-check-fixture',
    _project_key text,
    _activity_key text
);
CREATE TABLE reporting.actvtype (
    _source_system text NOT NULL,
    actv_code_type_id integer,
    actv_code_type text,
    actv_code_type_scope text,
    proj_id integer,
    delete_date timestamp,
    delete_session_id integer
);
CREATE TABLE reporting.actvcode (
    _source_system text NOT NULL,
    actv_code_id integer,
    actv_code_type_id integer,
    short_name text,
    actv_code_name text,
    seq_num integer,
    delete_date timestamp,
    delete_session_id integer
);
-- No uniqueness constraint: duplicate raw assignments must not inflate counts.
CREATE TABLE reporting.taskactv (
    _source_system text NOT NULL,
    proj_id integer,
    task_id integer,
    actv_code_type_id integer,
    actv_code_id integer,
    delete_date timestamp,
    delete_session_id integer
);

INSERT INTO reporting.project (_source_system, proj_id, proj_short_name, project_flag,
                               delete_date, _project_key) VALUES
('p6_22', 100, 'P22-PROGRAMME', 'Y', NULL, 'p6_22:100'),
('p6_23', 100, 'P23-PROGRAMME', 'Y', NULL, 'p6_23:100'),
('p6_22', 101, 'ARCHIVED',      'Y', NULL, 'p6_22:101'),
('p6_22', 102, 'DRAFT',         'Y', NULL, 'p6_22:102'),
('p6_22', 103, 'EPS-NODE',      'N', NULL, 'p6_22:103'),
('p6_22', 104, 'DELETED',       'Y', '2026-10-28', 'p6_22:104'),
('p6_22', 105, 'EMPTY',         'Y', NULL, 'p6_22:105'),
('p6_22', 106, 'DELETED-STATE', 'Y', NULL, 'p6_22:106');

INSERT INTO reporting.pcattype (_source_system, proj_catg_type_id, proj_catg_type)
SELECT 'p6_22', type_id, type_name
FROM (VALUES (1, 'Project State'), (2, 'Project Status'), (3, 'Project ID'),
             (4, 'Account'), (5, 'Lead Planner'), (6, 'Period')) t(type_id, type_name);
-- Source 23 deliberately has no Project ID or Period code type.
INSERT INTO reporting.pcattype (_source_system, proj_catg_type_id, proj_catg_type)
SELECT 'p6_23', type_id, type_name
FROM (VALUES (1, 'Project State'), (2, 'Project Status'),
             (4, 'Account'), (5, 'Lead Planner')) t(type_id, type_name);

INSERT INTO reporting.pcatval
(_source_system, proj_catg_id, proj_catg_type_id, proj_catg_short_name, proj_catg_name) VALUES
('p6_22', 1001, 1, 'L', 'Live'),
('p6_22', 1002, 1, 'A', 'Archived'),
('p6_22', 2001, 2, 'CS', 'Client Submitted'),
('p6_22', 2002, 2, 'D', 'Draft'),
('p6_22', 3001, 3, 'EXT-ALPHA', 'External Project Alpha'),
('p6_22', 3005, 3, 'EXT-EMPTY', 'External Empty Project'),
('p6_22', 4001, 4, 'NR', 'Network Rail'),
('p6_22', 5001, 5, 'MP', 'Mat Planner'),
('p6_22', 6001, 6, 'P01', 'Period 1'),
('p6_23', 1001, 1, 'L', 'Live'),
('p6_23', 2001, 2, 'CS', 'Client Submitted'),
('p6_23', 4001, 4, 'AMEY', 'Amey'),
('p6_23', 5001, 5, 'SP', 'Sam Planner');

INSERT INTO reporting.projpcat (_source_system, proj_id, proj_catg_type_id, proj_catg_id)
SELECT 'p6_22', p, type_id, value_id
FROM (VALUES (100), (101), (102), (103), (104), (105), (106)) projects(p)
CROSS JOIN (VALUES (1, 1001), (2, 2001), (4, 4001), (5, 5001), (6, 6001)) codes(type_id, value_id);
UPDATE reporting.projpcat SET proj_catg_id = 1002 WHERE _source_system = 'p6_22' AND proj_id = 101 AND proj_catg_type_id = 1;
UPDATE reporting.projpcat SET proj_catg_id = 2002 WHERE _source_system = 'p6_22' AND proj_id = 102 AND proj_catg_type_id = 2;
UPDATE reporting.projpcat SET delete_date = '2026-10-28' WHERE _source_system = 'p6_22' AND proj_id = 106 AND proj_catg_type_id = 1;
INSERT INTO reporting.projpcat (_source_system, proj_id, proj_catg_type_id, proj_catg_id) VALUES
('p6_22', 100, 3, 3001), ('p6_22', 105, 3, 3005),
('p6_23', 100, 1, 1001), ('p6_23', 100, 2, 2001),
('p6_23', 100, 4, 4001), ('p6_23', 100, 5, 5001),
-- Repeated classification rows must not multiply activities.
('p6_22', 100, 4, 4001);

INSERT INTO reporting.task
(_source_system, proj_id, task_id, task_code, task_name, total_float_hr_cnt,
 driving_path_flag, _project_key, _activity_key)
SELECT 'p6_22', 100, task_id, 'A' || task_id, 'Synthetic activity ' || task_id,
       float_hours, path_flag, 'p6_22:100', 'p6_22:100:' || task_id
FROM (VALUES
 (1, 0::numeric, 'Y'), (2, 159.99, 'N'), (3, 160, NULL), (4, -8, 'Y'),
 (5, NULL, ''), (6, 8, 'N'), (7, 0, 'Y'), (8, 0, 'Y'), (9, 0, 'Y'),
 (10, 0, 'N'), (11, -0.01, 'N'), (12, 16, NULL), (13, 24, 'N'), (14, 32, 'N')
) t(task_id, float_hours, path_flag);
UPDATE reporting.task SET delete_date = '2026-10-28' WHERE _source_system = 'p6_22' AND task_id = 8;
UPDATE reporting.task SET delete_session_id = 42 WHERE _source_system = 'p6_22' AND task_id = 9;
UPDATE reporting.task SET task_type = 'TT_Mile', status_code = 'TK_Complete' WHERE _source_system = 'p6_22' AND task_id = 10;
INSERT INTO reporting.task
(_source_system, proj_id, task_id, task_code, task_name, total_float_hr_cnt,
 driving_path_flag, _project_key, _activity_key) VALUES
('p6_23', 100, 1, 'A1', 'Source collision activity', 0, 'Y', 'p6_23:100', 'p6_23:100:1'),
('p6_23', 100, 2, 'A2', 'Other float activity', 200, 'N', 'p6_23:100', 'p6_23:100:2');
-- These activities must never reach the outputs, despite matching IDs/codes.
INSERT INTO reporting.task
(_source_system, proj_id, task_id, task_code, total_float_hr_cnt, driving_path_flag, _project_key, _activity_key)
SELECT 'p6_22', p, 1, 'EXCLUDED', 0, 'Y', 'p6_22:' || p, 'p6_22:' || p || ':1'
FROM (VALUES (101), (102), (103), (104), (106)) excluded(p);

INSERT INTO reporting.actvtype
(_source_system, actv_code_type_id, actv_code_type, actv_code_type_scope, proj_id) VALUES
('p6_22', 10, 'Project Stage', 'AS_Global', NULL),
('p6_22', 11, 'Project Stage', 'AS_Project', 100),
('p6_22', 20, 'Discipline', 'AS_Global', NULL),
('p6_22', 30, 'Milestone Type', 'AS_Global', NULL),
('p6_22', 40, 'Responsible Company', 'AS_Global', NULL),
-- DEL is intentionally a project-scoped status; it still excludes the task.
('p6_22', 90, 'Activity Status', 'AS_Project', 100),
('p6_23', 10, 'Project Stage', 'AS_Global', NULL),
('p6_23', 21, 'Discipline - Global', 'AS_Global', NULL),
('p6_23', 41, 'Company - Global', 'AS_Global', NULL);
-- Design Key is absent in both sources. Responsible Company is absent in 23.
INSERT INTO reporting.actvcode
(_source_system, actv_code_id, actv_code_type_id, short_name, actv_code_name, seq_num) VALUES
('p6_22', 101, 10, 'ES3', 'ES3 - Option Selection', 1),
('p6_22', 102, 10, 'ES4', 'ES4 – Approval in Principle Design', 2),
('p6_22', 103, 10, ' ', '   ', 3),
('p6_22', 104, 10, 'OLD', 'ES2 - Deleted stage', 4),
('p6_22', 105, 11, 'PROJECT', 'Project-only stage', 5),
('p6_22', 106, 10, 'ES5/ES6', 'ES5/ES6 — Detailed Design / Construction', 6),
('p6_22', 201, 20, 'SIG', 'Signalling', 1),
('p6_22', 202, 20, 'CIV', 'Civils', 2),
('p6_22', 203, 20, '', ' ', 3),
('p6_22', 204, 20, 'TEL', 'Telecoms', 4),
('p6_22', 301, 30, 'CONTRACT', 'Contract Milestones', 1),
('p6_22', 401, 40, 'NR', 'Network Rail', 1),
('p6_22', 402, 40, 'AMEY', 'Amey Consulting', 2),
('p6_22', 900, 90, 'DEL', 'Deleted / Retired', 1),
('p6_23', 101, 10, 'ES3', 'ES3 - Source 23 Stage', 1),
('p6_23', 201, 21, 'ETCS', 'ETCS', 1),
('p6_23', 401, 41, 'OTHER', 'Unapproved Company Alias', 1);
UPDATE reporting.actvcode SET delete_date = '2026-10-28' WHERE _source_system = 'p6_22' AND actv_code_id = 104;

INSERT INTO reporting.taskactv
(_source_system, proj_id, task_id, actv_code_type_id, actv_code_id) VALUES
('p6_22', 100, 1, 10, 101),
('p6_22', 100, 2, 10, 102),
('p6_22', 100, 4, 10, 101), ('p6_22', 100, 4, 10, 102),
('p6_22', 100, 5, 10, 999), -- dangling value
('p6_22', 100, 6, 10, 103), -- blank dictionary value
('p6_22', 100, 7, 90, 900), -- retired, project-scoped status
('p6_22', 100, 8, 10, 101), ('p6_22', 100, 9, 10, 101),
('p6_22', 100, 10, 10, 101), ('p6_22', 100, 10, 10, 103), -- one valid plus blank
('p6_22', 100, 11, 10, 101), ('p6_22', 100, 11, 10, 101), -- duplicate
('p6_22', 100, 12, 10, 102), -- assignment itself deleted below
('p6_22', 100, 13, 10, 104), -- dictionary value deleted
('p6_22', 100, 14, 11, 105), -- same name, wrong scope
('p6_22', 100, 1, 20, 201), ('p6_22', 100, 2, 20, 202),
('p6_22', 100, 3, 20, 203),
('p6_22', 100, 5, 20, 201), ('p6_22', 100, 5, 20, 201),
('p6_22', 100, 1, 30, 301),
('p6_22', 100, 1, 40, 401), ('p6_22', 100, 2, 40, 402),
('p6_23', 100, 1, 10, 101), ('p6_23', 100, 1, 21, 201),
('p6_23', 100, 1, 41, 401);
UPDATE reporting.taskactv SET delete_date = '2026-10-28'
WHERE _source_system = 'p6_22' AND proj_id = 100 AND task_id = 12 AND actv_code_type_id = 10;

-- Model a stable publication view over a private generation, as in production.
CREATE SCHEMA fixture_generation_a;
ALTER TABLE reporting.actvcode SET SCHEMA fixture_generation_a;
CREATE VIEW reporting.actvcode AS SELECT * FROM fixture_generation_a.actvcode;
-- Production's publisher already grants the raw stable views to this reader.
GRANT USAGE ON SCHEMA reporting TO p6_reporting_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO p6_reporting_reader;

-- Synthetic raw objects are installer-owned; give the sync role the same
-- grant capability as on its own raw views in production.
GRANT USAGE ON SCHEMA reporting TO p6_reporting_sync;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO p6_reporting_sync WITH GRANT OPTION;
