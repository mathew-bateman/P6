"""Safety and interrupted-commit regression tests; no database access."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ops.recovery import cleanup_reporting_remnants as cleanup


def obj(identifier,schema,name,kind="U",parent=0):
    return {"object_id":identifier,"schema_name":schema,"name":name,"type":kind,
        "parent_object_id":parent,"parent_schema":schema if parent else None,
        "parent_name":"retired_table" if parent else None,"definition_sha256":"definition",
        "is_encrypted":0,"created_at":"2025-01-01","modified_at":"2025-01-01"}


class ScopeSafetyTests(unittest.TestCase):
    def test_allowlist_is_exact_and_excludes_p6_and_recovery_tables(self):
        self.assertEqual(len(cleanup.P622_TABLES),18)
        self.assertEqual(len(cleanup.DJANGO_TABLES),10)
        names={name for source in cleanup.SOURCES.values() for _,name in source["tables"]}
        for name in ("PROJECT","TASK","TASKPRED","USERDATA","maintenance_userdata_backup","mtn_ud_rebecca_20260821"):
            self.assertNotIn(name,names)

    def test_native_report_and_maintenance_objects_remain_protected(self):
        objects=[obj(1,"powerbitables","retired_table"),obj(2,"powerbitables","old_fk","F",1),
                 obj(3,"dbo","WBSRSRC_ASSGN_BY_PROJ_BY_MONTH","V"),obj(4,"dbo","maintenance_userdata_backup")]
        tables,removed=cleanup.scoped_objects(objects,{("powerbitables","retired_table")})
        self.assertEqual(tables,{1});self.assertEqual(removed,{1,2})
        protected=cleanup.protected_objects(objects,removed)
        self.assertEqual({r["name"] for r in protected},{"WBSRSRC_ASSGN_BY_PROJ_BY_MONTH","maintenance_userdata_backup"})

    def test_missing_table_or_unreviewed_attached_trigger_blocks_cleanup(self):
        with self.assertRaises(RuntimeError):
            cleanup.scoped_objects([],{("powerbitables","retired_table")})
        with self.assertRaises(RuntimeError):
            cleanup.scoped_objects([obj(1,"powerbitables","retired_table"),obj(2,"powerbitables","surprise","TR",1)],{("powerbitables","retired_table")})

    def test_schema_and_foreign_key_targets_cannot_escape_allowlist(self):
        expected={"schemas":[{"name":s,"owner":"dbo"} for s in cleanup.SOURCES["p6_22"]["schemas"]],"foreign_keys":[]}
        cleanup.validate_removal_scope("p6_22",expected)
        with self.assertRaises(RuntimeError):
            cleanup.validate_removal_scope("p6_22",dict(expected,schemas=[{"name":"dbo","owner":"dbo"}]))
        with self.assertRaises(RuntimeError):
            cleanup.validate_removal_scope("p6_22",dict(expected,foreign_keys=[{"parent_schema":"dbo","parent_name":"TASK"}]))

    def test_case_insensitive_unresolved_dependency_blocks_cleanup(self):
        def query(connection,statement,parameters=()):
            if "SELECT referencing_id,referenced_id" in statement:
                return [{"referencing_id":999,"referenced_id":None,"referenced_schema_name":"POWERBITABLES","referenced_entity_name":"XERTOOLKIT_SETTINGS_THRESHOLDS"}]
            if "SELECT DB_NAME()" in statement:return [{"n":"P62212_1"}]
            return []
        with patch.object(cleanup,"sql_query",side_effect=query):
            with self.assertRaisesRegex(RuntimeError,"outside the removal allowlist"):
                cleanup.validate_dependencies(None,"p6_22",{1},{1})

    def test_encrypted_native_module_is_preserved_as_opaque_metadata(self):
        row=dict(obj(1,"dbo","ACTIVITY_COPY_API","P"),definition=None,is_encrypted=1)
        row.pop("definition_sha256")
        with patch.object(cleanup,"sql_query",return_value=[row]):
            result=cleanup.object_catalogue(None)
        self.assertIsNone(result[0]["definition_sha256"])
        protected=cleanup.protected_objects(result,set())
        self.assertEqual(protected[0]["is_encrypted"],1)
        self.assertEqual(protected[0]["name"],"ACTIVITY_COPY_API")

    def test_unexplained_missing_executable_definition_still_fails_closed(self):
        for encrypted in (None,0):
            row=dict(obj(1,"dbo","native_procedure","P"),definition=None,is_encrypted=encrypted)
            row.pop("definition_sha256")
            with self.subTest(encrypted=encrypted),patch.object(cleanup,"sql_query",return_value=[row]),self.assertRaises(RuntimeError):
                cleanup.object_catalogue(None)


class JournalSafetyTests(unittest.TestCase):
    def record(self):
        return {"database":"P62212_1","machine":"xps4-p62212","capture_sha256":"capture",
            "tables_removed":18,"schemas_removed":["powerbitables","xertoolkit_rollback"],
            "protected_catalogue_sha256":"native"}

    def test_completed_journal_is_bound_to_capture_identity_scope_and_native_state(self):
        cleanup.validate_completed_journal(self.record(),"p6_22",{"capture_sha256":"capture"},"native")
        for key,bad in [("database","Axial"),("machine","other"),("capture_sha256","old"),
                        ("tables_removed",99),("schemas_removed",["dbo"]),("protected_catalogue_sha256","changed")]:
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                cleanup.validate_completed_journal(dict(self.record(),**{key:bad}),"p6_22",{"capture_sha256":"capture"},"native")

    def test_missing_journal_can_be_recovered_only_from_complete_absent_scope(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(cleanup,"identity"), \
                patch.object(cleanup,"object_catalogue",return_value=[]),patch.object(cleanup,"sql_query",return_value=[]), \
                patch.object(cleanup,"protected_after",return_value="native"):
            state=cleanup.preflight_target(None,"p6_22",{}, {"capture_sha256":"capture"},Path(temporary))
        self.assertTrue(state["complete"])
        self.assertTrue(state["record"]["resumed_completed_state_verified"])
        cleanup.validate_completed_journal(state["record"],"p6_22",{"capture_sha256":"capture"},"native")

    def test_changed_native_catalogue_blocks_recovered_completion(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(cleanup,"identity"), \
                patch.object(cleanup,"object_catalogue",return_value=[]),patch.object(cleanup,"sql_query",return_value=[]), \
                patch.object(cleanup,"protected_after",side_effect=RuntimeError("native changed")):
            with self.assertRaises(RuntimeError):
                cleanup.preflight_target(None,"p6_22",{}, {"capture_sha256":"capture"},Path(temporary))

    def test_returned_tables_with_completed_journal_are_not_deleted_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/"applied-p6_22.json").write_text(json.dumps(self.record()))
            with patch.object(cleanup,"identity"),patch.object(cleanup,"object_catalogue",return_value=[obj(1,"powerbitables","xertoolkit_settings_thresholds")]), \
                    patch.object(cleanup,"sql_query",return_value=[]),patch.object(cleanup,"source_snapshot") as capture:
                with self.assertRaisesRegex(RuntimeError,"have returned"):
                    cleanup.preflight_target(None,"p6_22",{}, {"capture_sha256":"capture"},root)
                capture.assert_not_called()


class ApplyOrderingAndRecoveryTests(unittest.TestCase):
    @staticmethod
    def query(connection,statement,parameters=()):
        return [{"n":0}] if statement.startswith("DECLARE @r") else []

    @staticmethod
    def snapshot(code):
        return {"schemas":[{"name":s,"owner":"dbo"} for s in cleanup.SOURCES[code]["schemas"]]}

    def test_second_database_preflight_failure_prevents_every_drop_and_commit(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(cleanup,"load_capture",return_value=({"sources":{c:{} for c in cleanup.SOURCES}},{"capture_sha256":"capture"})), \
                patch.object(cleanup,"backup_gate"),patch.object(cleanup,"get_connection",side_effect=[MagicMock(),MagicMock()]), \
                patch.object(cleanup,"sql_query",side_effect=self.query) as query, \
                patch.object(cleanup,"preflight_target",side_effect=[{"complete":False,"snapshot":{}},RuntimeError("second target changed")]), \
                patch.object(cleanup,"remove_exact") as remove:
            with self.assertRaisesRegex(RuntimeError,"second target changed"):
                cleanup.apply(Path(temporary))
            remove.assert_not_called()
            self.assertFalse(any(call.args[1]=="COMMIT TRANSACTION" for call in query.call_args_list))

    def test_partial_commit_has_a_durable_capture_bound_journal_and_resumes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);receipt={"capture_sha256":"capture"};data={"sources":{c:{} for c in cleanup.SOURCES}}
            first_states=[{"complete":False,"snapshot":self.snapshot(c)} for c in cleanup.SOURCES]
            patches=lambda: (patch.object(cleanup,"load_capture",return_value=(data,receipt)),patch.object(cleanup,"backup_gate"),
                patch.object(cleanup,"get_connection",side_effect=[MagicMock(),MagicMock()]),patch.object(cleanup,"sql_query",side_effect=self.query),
                patch.object(cleanup,"core_counts",return_value={"projects":3,"activities":9,"relationships":12}))
            p=patches()
            with p[0],p[1],p[2],p[3],p[4],patch.object(cleanup,"preflight_target",side_effect=first_states), \
                    patch.object(cleanup,"remove_exact",side_effect=["native22",RuntimeError("second DDL failed")]):
                with self.assertRaisesRegex(RuntimeError,"second DDL failed"):
                    cleanup.apply(root)
            saved=json.loads((root/"applied-p6_22.json").read_text())
            cleanup.validate_completed_journal(saved,"p6_22",receipt,"native22")
            self.assertFalse((root/"cleanup-result.json").exists())
            p=patches()
            with p[0],p[1],p[2],p[3],p[4],patch.object(cleanup,"preflight_target",side_effect=[
                    {"complete":True,"record":saved,"journal_exists":True},
                    {"complete":False,"snapshot":self.snapshot("p6_23")}]), \
                    patch.object(cleanup,"remove_exact",return_value="native23") as remove:
                result=cleanup.apply(root)
            self.assertEqual(result["status"],"complete")
            self.assertEqual(set(result["sources"]),set(cleanup.SOURCES))
            self.assertEqual(remove.call_count,1)
            self.assertEqual(remove.call_args.args[1],"p6_23")


if __name__=="__main__":
    unittest.main()
