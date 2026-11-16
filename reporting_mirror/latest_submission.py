"""Select one submitted programme for every logical project at publication.

Calculation generations and raw P6 tables retain their complete history. Only
the project-linked XERToolkit report contracts use this publication scope.
"""
import re

from psycopg import sql


POLICY = "latest_client_submitted_v1"
SELECTION_TABLE = "_latest_client_submitted_projects_v1"


def selection_query(schema):
    """Return source-local, deterministic programme selection over a generation.

    A dated submission takes precedence over an undated copy, even when that
    copy has a newer P6 data date. This preserves the legacy report selection.
    P6 23 uses Client Project ID where P6 22 uses Project ID. Numeric category
    identities avoid merging projects that happen to share a name/short code.
    """
    if not re.fullmatch(r"(?:load|native_test|native_shadow)_[0-9a-f]{16}_p6_(?:22|23)", schema):
        raise ValueError("Latest-submission selection requires a private generation schema.")
    return sql.SQL("""
WITH assignments AS (
    SELECT DISTINCT a.proj_id,
        CASE WHEN lower(btrim(t.proj_catg_type)) = 'projects status'
             THEN 'project status' ELSE lower(btrim(t.proj_catg_type)) END AS kind,
        t.proj_catg_type_id, v.proj_catg_id,
        lower(btrim(v.proj_catg_name)) AS code_value
    FROM {s}.projpcat a
    JOIN {s}.pcattype t ON t.proj_catg_type_id = a.proj_catg_type_id
    JOIN {s}.pcatval v ON v.proj_catg_type_id = a.proj_catg_type_id
        AND v.proj_catg_id = a.proj_catg_id
    WHERE a.delete_date IS NULL AND a.delete_session_id IS NULL
        AND t.delete_date IS NULL AND t.delete_session_id IS NULL
        AND v.delete_date IS NULL AND v.delete_session_id IS NULL
        AND lower(btrim(t.proj_catg_type)) IN
            ('project id', 'client project id', 'project status', 'projects status')
), codes AS (
    SELECT proj_id,
        count(*) FILTER (WHERE kind = 'project id') AS project_ids,
        max(proj_catg_type_id::text || ':' || proj_catg_id::text)
            FILTER (WHERE kind = 'project id') AS project_identity,
        count(*) FILTER (WHERE kind = 'client project id') AS client_project_ids,
        max(proj_catg_type_id::text || ':' || proj_catg_id::text)
            FILTER (WHERE kind = 'client project id') AS client_project_identity,
        count(*) FILTER (WHERE kind = 'project status') > 0
            AND bool_and(coalesce(code_value = 'client submitted', false))
                FILTER (WHERE kind = 'project status') AS is_submitted
    FROM assignments GROUP BY proj_id
), candidates AS (
    SELECT p.proj_id,
        CASE WHEN c.project_ids = 1 THEN 'code:' || c.project_identity
             WHEN c.project_ids = 0 AND c.client_project_ids = 1
                THEN 'code:' || c.client_project_identity
             ELSE 'project:' || p.proj_id::text END AS project_identity,
        d."Updated Date" AS updated_date, d.data_date,
        p.update_date AS source_update_date,
        CASE WHEN d."Updated Date" IS NOT NULL THEN 'updated_date'
             WHEN d.data_date IS NOT NULL THEN 'data_date'
             ELSE 'source_update_date_or_project_id' END AS selection_basis
    FROM {s}.project p
    JOIN codes c ON c.proj_id = p.proj_id AND c.is_submitted
    JOIN {s}.xertoolkit_vw_pbi_projects d ON d.proj_id = p.proj_id
    WHERE p.project_flag = 'Y' AND coalesce(p.orig_proj_id, 0) = 0
        AND p.delete_date IS NULL AND p.delete_session_id IS NULL
), ranked AS (
    SELECT *, row_number() OVER (
        PARTITION BY project_identity
        ORDER BY updated_date DESC NULLS LAST, data_date DESC NULLS LAST,
            source_update_date DESC NULLS LAST, proj_id DESC
    ) AS selection_rank FROM candidates
)
SELECT proj_id, project_identity, updated_date, data_date, selection_basis
FROM ranked WHERE selection_rank = 1
""").format(s=sql.Identifier(schema))


def scopes_to_latest_submission(entry):
    return entry["name"].startswith("xertoolkit_vw_pbi_") and any(
        c["name"] == "proj_id" for c in entry.get("columns", ())
    )


def prepare_latest_submissions(pg, schema):
    """Materialise the small selection once before publishing any report view."""
    query = selection_query(schema)
    pg.execute(sql.SQL("CREATE TABLE {}.{} AS {}").format(
        sql.Identifier(schema), sql.Identifier(SELECTION_TABLE), query))
    for field in ("proj_id", "project_identity"):
        pg.execute(sql.SQL("CREATE UNIQUE INDEX ON {}.{} ({})").format(
            sql.Identifier(schema), sql.Identifier(SELECTION_TABLE), sql.Identifier(field)))
    pg.execute(sql.SQL("ANALYZE {}.{}").format(
        sql.Identifier(schema), sql.Identifier(SELECTION_TABLE)))


def publication_predicate(schema):
    return sql.SQL(" WHERE EXISTS (SELECT 1 FROM {}.{} selected WHERE selected.proj_id=t.proj_id)").format(
        sql.Identifier(schema), sql.Identifier(SELECTION_TABLE))


def scope_manifest_entry(pg, schema, entry):
    """Keep recovery manifests consistent with the public reporting contract."""
    if not scopes_to_latest_submission(entry):
        return
    entry["unfiltered_rows"] = entry["rows"]
    # Legacy copied SQL outputs can also contain a full-generation digest.
    if "sha256_sum" in entry:
        entry["unfiltered_sha256_sum"] = entry.pop("sha256_sum")
    entry["rows"] = pg.execute(sql.SQL("SELECT count(*) n FROM {}.{} t").format(
        sql.Identifier(schema), sql.Identifier(entry["name"])) + publication_predicate(schema)).fetchone()["n"]
    entry["publication_scope"] = POLICY
