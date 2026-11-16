"""Small deterministic edge-case checks against the real PostgreSQL calculator."""
from datetime import datetime
from decimal import Decimal
import json
import uuid

from psycopg import sql

from reporting_mirror.native_engine import calculate, generation_schema
from reporting_mirror.native_settings import active_version, copy_settings, insert_row
from reporting_mirror.sql_staging import RAW_TABLES


def run_fixture_checks(pg, validation_id):
    token = uuid.uuid5(uuid.UUID(str(validation_id)), "native-fixtures").hex[:16]
    schema = "native_test_" + token + "_p6_22"
    s = generation_schema(schema)
    results = []
    def check(name, condition):
        if not condition:
            raise RuntimeError("Native fixture failed: " + name)
        results.append(name)
    # A savepoint guarantees fixture data cannot survive success or failure.
    class FixtureComplete(Exception):
        pass
    try:
        with pg.transaction():
            pg.execute("SET LOCAL statement_timeout='3min'")
            pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            for name in sorted(RAW_TABLES):
                table = name.lower()
                if table == "obsproj" and not pg.execute("SELECT to_regclass('p6_22.obsproj') present").fetchone()["present"]:
                    pg.execute(f"CREATE TABLE {s}.obsproj(obs_id integer,proj_id integer,wbs_id integer)")
                else:
                    columns = pg.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='p6_22' AND table_name=%s AND left(column_name,1)<>'_' ORDER BY ordinal_position", (table,)).fetchall()
                    pg.execute(sql.SQL("CREATE TABLE {}.{} AS SELECT {} FROM p6_22.{} LIMIT 0").format(sql.Identifier(schema), sql.Identifier(table), sql.SQL(",").join(sql.Identifier(c["column_name"]) for c in columns), sql.Identifier(table)))
            selected = active_version(pg); version = selected["config_version_id"]
            copy_settings(pg, schema, version)
            pg.execute(f"UPDATE {s}.xertoolkit_schedule_quality_check_scope SET is_enabled=1,include_loe=0,include_wbs_summary=0,include_milestones=0,exclude_complete=1 WHERE config_version_id=%s", (version,))
            for option, value in {"high_float_days":84,"high_duration_days":84,"negative_float_days":0,"near_critical_upper_days":20,"riding_days_after_data_date":2}.items():
                pg.execute(f"UPDATE {s}.xertoolkit_schedule_quality_option SET numeric_value=%s WHERE config_version_id=%s AND option_code=%s", (value,version,option))
            pg.execute(f"UPDATE {s}.xertoolkit_schedule_quality_option SET bit_value=1 WHERE config_version_id=%s AND bit_value IS NOT NULL", (version,))
            insert_row(pg,"project",{"proj_id":1,"proj_short_name":"FIXTURE","project_flag":"Y","last_recalc_date":datetime(2026,1,10)},schema=schema)
            insert_row(pg,"projwbs",{"proj_id":1,"wbs_id":1,"wbs_name":"Update - 24/10/26"},schema=schema)
            insert_row(pg,"obsproj",{"proj_id":1,"wbs_id":1,"obs_id":1},schema=schema)
            tasks={
                1:{"total_float_hr_cnt":672,"remain_drtn_hr_cnt":672},
                2:{"total_float_hr_cnt":Decimal("671.99"),"remain_drtn_hr_cnt":673},
                3:{"total_float_hr_cnt":-1},4:{"total_float_hr_cnt":4},
                5:{"total_float_hr_cnt":160},6:{"total_float_hr_cnt":Decimal("160.01")},
                7:{"task_type":"TT_LOE","total_float_hr_cnt":1000},
                8:{"task_type":"TT_WBS","total_float_hr_cnt":1000},
                9:{"status_code":"TK_Complete","total_float_hr_cnt":1000},
                10:{"task_type":"TT_Mile","total_float_hr_cnt":1000},
                11:{"delete_session_id":1,"total_float_hr_cnt":1000},
                12:{"early_start_date":datetime(2026,1,9)},
                13:{"act_start_date":datetime(2026,1,9),"phys_complete_pct":0,"status_code":"TK_Active"},
                14:{"early_start_date":datetime(2026,1,10)},15:{},16:{},17:{},18:{},
            }
            for identifier, overrides in tasks.items():
                row={"proj_id":1,"task_id":identifier,"wbs_id":1,"task_code":"A"+str(identifier),"task_name":"Fixture "+str(identifier),"task_type":"TT_Task","status_code":"TK_NotStart","total_float_hr_cnt":0,"remain_drtn_hr_cnt":8,"target_drtn_hr_cnt":8,"phys_complete_pct":0,**overrides}
                insert_row(pg,"task",row,schema=schema)
            for relation,(predecessor,successor,kind,lag) in enumerate([(1,2,"PR_FS",0),(15,15,"PR_FS",0),(16,17,"PR_FS",0),(17,16,"PR_FS",0),(2,18,"PR_SS",8)],1):
                insert_row(pg,"taskpred",{"task_pred_id":relation,"proj_id":1,"pred_proj_id":1,"pred_task_id":predecessor,"task_id":successor,"pred_type":kind,"lag_hr_cnt":lag},schema=schema)
            insert_row(pg,'udftype',{'udf_type_id':99999,'table_name':'TASK','udf_type_name':'Fixture','udf_type_label':'Fixture'},schema=schema)
            insert_row(pg,'udfvalue',{'udf_type_id':99999,'fk_id':1,'proj_id':1,'table_name':'TASK','udf_text':'Verified UDF'},schema=schema)
            insert_row(pg,'actvtype',{'actv_code_type_id':99999,'actv_code_type':'Fixture'},schema=schema)
            insert_row(pg,'actvcode',{'actv_code_id':99999,'actv_code_type_id':99999,'actv_code_name':'Verified code'},schema=schema)
            insert_row(pg,'taskactv',{'proj_id':1,'task_id':1,'actv_code_type_id':99999,'actv_code_id':99999},schema=schema)
            pg.execute(f"DELETE FROM {s}.xertoolkit_schedule_quality_detail_field WHERE config_version_id=%s",(version,))
            for index,(category,field,label,display) in enumerate([('task_column','total_float_hr_cnt','Float','p6_hours_and_days'),('udf','99999','UDF','native'),('activity_code','99999','Code','native')],1):
                insert_row(pg,'xertoolkit_schedule_quality_detail_field',{'detail_field_id':999990+index,'config_version_id':version,'check_code':'high_float','source_category':category,'source_identifier':field,'display_label':label,'sort_order':index,'display_format':display},schema=schema)
            manifest,quality=calculate(pg,schema,version,999999)
            rows={r["task_id"]:r for r in pg.execute(f"SELECT * FROM {s}.xertoolkit_fn_activity_quality").fetchall()}
            check("high float inclusive boundary", rows[1]["is_high_float"]==1 and rows[2]["is_high_float"]==0)
            check("duration exclusive boundary", rows[1]["is_high_duration"]==0 and rows[2]["is_high_duration"]==1)
            check("negative float and critical boundary", rows[3]["is_negative_float"]==1 and rows[3]["is_critical_task"]==1 and rows[4]["is_critical_task"]==0)
            check("near critical inclusive bounds",rows[4]["is_near_critical_task"]==1 and rows[5]["is_near_critical_task"]==1 and rows[6]["is_near_critical_task"]==0)
            check("LOE WBS complete milestone scopes", all(i not in rows for i in (7,8,9,10)))
            check("deleted task excluded",11 not in rows)
            check("invalid date and progress error", rows[12]["is_invalid_date"]==1 and rows[13]["is_in_progress_error"]==1)
            check("riding data date",rows[14]["is_riding_progress_date"]==1)
            loops=pg.execute(f"SELECT task_id,calculation_method FROM {s}.xertoolkit_result_logic_loop_tasks ORDER BY task_id").fetchall()
            check("self loop cycle and acyclic separation",[(r["task_id"],r["calculation_method"]) for r in loops]==[(15,"self_loop"),(16,"cycle_core_prune"),(17,"cycle_core_prune")])
            dates=pg.execute(f"SELECT {s}.p6_try_date('31/02/26') invalid,{s}.p6_try_date('24/10/26') valid,{s}.p6_try_date('01/01/50') cutoff,{s}.p6_add_days('2026-01-10'::date,1.9) added").fetchone()
            check("strict UK dates cutoff and fractional days", dates["invalid"] is None and str(dates["valid"])=="2026-10-24" and str(dates["cutoff"])=="1950-01-01" and str(dates["added"])=="2026-01-11")
            check("twenty outputs and ten evidence reconciliations",len(manifest)==20 and quality["evidence_count_checks_passed"]==10)
            check("native materialized output storage",pg.execute("SELECT COUNT(*) n FROM pg_matviews WHERE schemaname=%s",(schema,)).fetchone()["n"]==20)
            check("source business update date",str(pg.execute(f'SELECT "Updated Date" value FROM {s}.xertoolkit_vw_pbi_projects').fetchone()["value"])=="2026-10-24")
            evidence=pg.execute(f"SELECT detail_json FROM {s}.xertoolkit_result_schedule_quality_task_evidence WHERE task_id=1 AND check_code='high_float'").fetchone()
            detail=json.loads(evidence['detail_json'])
            check('configured hours and eight hour days',detail['Float']=='672.00 hours | 84.00 days (8h/day)')
            check('native UDF and activity code evidence',detail['UDF']=='Verified UDF' and detail['Code']=='Verified code')
            # Deliberately roll back successful fixture construction too.
            raise FixtureComplete()
    except FixtureComplete:
        pass
    return results
