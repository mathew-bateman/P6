"""Guardrail tests for the destructive SQL retirement allowlist."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("retire_sql_reporting", Path(__file__).with_name("retire_sql_reporting.py"))
retire = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retire)


class RetirementGuards(unittest.TestCase):
    def checkpoint(self, root):
        directory = Path(root) / ".recovery" / "20261116T120000Z"
        directory.mkdir(parents=True)
        (directory / "application/sql").mkdir(parents=True)

        def save(name, value):
            path = directory / name
            path.write_text(json.dumps(value))
            return path

        sql_rows, proofs = [], []
        for slug, database, machine in (("axialp6", "Axial", "xps4-axialp6"),
                                         ("axial-training", "Axial_Training", "xps4-axialp6"),
                                         ("p62212", retire.DATABASE, retire.MACHINE)):
            name = "application/sql/" + slug + ".bak"
            path = directory / name
            path.write_bytes(("test backup " + slug).encode())
            checksum = retire.file_digest(path)
            sql_rows.append({"target_slug": slug, "database": database, "path": name,
                "sha256": checksum, "copy_only": True, "checksum": True,
                "restore_verifyonly": "passed", "row_counts_after": {"project_count": 1, "task_count": 2, "taskpred_count": 3},
                "source_machine": {"database_name": database, "machine_name": machine}})
            proofs.append({"target_slug": slug, "database": database, "backup_sha256": checksum,
                "counts": {"projects": 1, "activities": 2, "relationships": 3},
                "restore_with_checksum": "passed", "dbcc_checkdb_physical_only": "passed",
                "object_catalogue_and_module_hashes": "matched",
                "retirement_rehearsal": {"status": "passed", "objects_removed": len(retire.ALLOWLIST),
                    "transaction_rolled_back": True, "source_row_counts_unchanged": True} if slug == "p62212" else None})
        for name in ("application/django.sqlite3", "application-source.tar.gz", "deployment-config.tar.gz",
                     "p6_reporting.dump", "postgres-globals.sql", "postgres-schema.sql", "docker-images.tar"):
            (directory / name).write_bytes(("test artifact " + name).encode())
        save("application/capture.json", {"sqlite": {"integrity_check": "ok"}, "sql_backups": sql_rows})
        files = [{"path": path.relative_to(directory).as_posix(), "size_bytes": path.stat().st_size,
                  "sha256": retire.file_digest(path)} for path in sorted(directory.rglob("*")) if path.is_file()]
        manifest = save("manifest.json", {"recovery_id": directory.name, "files": files})
        manifest_hash = retire.file_digest(manifest)
        archive, encrypted = directory / "recovery.tar.gz", directory / "recovery.tar.gz.enc"
        archive.write_bytes(b"archive")
        encrypted.write_bytes(b"encrypted archive")
        save("encryption_manifest.json", {"recovery_id": directory.name, "capture_manifest_sha256": manifest_hash,
            "archive_sha256": retire.file_digest(archive), "archive_bytes": archive.stat().st_size,
            "encrypted_sha256": retire.file_digest(encrypted), "encrypted_bytes": encrypted.stat().st_size})
        save("offsite_receipt.json", {"recovery_id": directory.name, "upload_download_decrypt_sha256": "passed",
            "archive_sha256": retire.file_digest(archive), "encrypted_sha256": retire.file_digest(encrypted),
            "archive_item_id": "archive-id", "metadata_item_id": "metadata-id"})
        save("restore-verification.json", {"status": "verified", "recovery_id": directory.name,
            "manifest_sha256": manifest_hash, "sqlite_integrity": "ok",
            "postgres": {"status": "passed", "globals_restored": True, "pg_restore_exit_on_error": "passed",
                "sqlite_integrity": "ok", "manifest_row_counts_checked": 100, "consumer_views_planned": 60},
            "sql": {"status": "passed", "databases": proofs}})
        backup = directory / "application/sql/p62212.bak"
        return {"status": "verified", "recovery_directory": str(directory), "sql_source": {
            "database": retire.DATABASE, "machine": retire.MACHINE, "restore_verified": True,
            "backup_path": str(backup), "sha256": retire.file_digest(backup)}}

    def retired_checkpoint(self, root, *, parallel=False, machine=retire.MACHINE, remaining=False):
        receipt = self.checkpoint(root)
        directory = Path(receipt["recovery_directory"])
        marker = {"status": "retired", "target": {"database": retire.DATABASE, "machine": machine}}
        raw = json.dumps(marker).encode()
        with tarfile.open(directory / "deployment-config.tar.gz", "w:gz") as archive:
            member = tarfile.TarInfo("state/sql-reporting-retired.json")
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
        (directory / "reporting-state.json").write_text(json.dumps({"config": [{
            "sql_parallel_enabled": parallel, "calculation_engine": "postgres",
            "manage_settings_in_postgres": True, "serve_reports": True, "pending_engine": ""}]}))
        capture_path = directory / "application/capture.json"
        capture = json.loads(capture_path.read_text())
        next(row for row in capture["sql_backups"] if row["target_slug"] == "p62212")["object_catalogue"] = [
            {"schema_name": retire.SCHEMA, "name": retire.VIEWS[0], "type": "V"}] if remaining else []
        capture_path.write_text(json.dumps(capture))
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        names = [row["path"] for row in manifest["files"]] + ["reporting-state.json"]
        manifest["files"] = [{"path": name, "size_bytes": (directory / name).stat().st_size,
                              "sha256": retire.file_digest(directory / name)} for name in names]
        manifest_path.write_text(json.dumps(manifest))
        manifest_hash = retire.file_digest(manifest_path)
        encryption_path = directory / "encryption_manifest.json"
        encryption = json.loads(encryption_path.read_text())
        encryption["capture_manifest_sha256"] = manifest_hash
        encryption_path.write_text(json.dumps(encryption))
        restore_path = directory / "restore-verification.json"
        restore = json.loads(restore_path.read_text())
        restore["manifest_sha256"] = manifest_hash
        proof = next(row for row in restore["sql"]["databases"] if row["target_slug"] == "p62212")
        proof["retirement_rehearsal"] = {"status": "already_retired", "removal_scope_objects": 0,
            "captured_retirement_marker_sha256": retire.hashlib.sha256(raw).hexdigest(),
            "captured_parallel_sql_enabled": False}
        restore_path.write_text(json.dumps(restore))
        return receipt

    def objects(self):
        return [{"name": name, "type": kind, "is_schema_bound": 0} for name, kind in retire.ALLOWLIST.items()]

    def test_scope_is_exact_and_excludes_original_p6_and_settings(self):
        self.assertEqual(len(retire.ALLOWLIST), 40)
        self.assertEqual(sum(kind == "U" for kind in retire.ALLOWLIST.values()), 4)
        self.assertTrue(all(name.startswith("xertoolkit_result_") for name, kind in retire.ALLOWLIST.items() if kind == "U"))
        retire.validate_scope(self.objects())

    def test_missing_new_or_retyped_object_requires_review(self):
        for modified in (self.objects()[:-1], self.objects() + [{"name": "unreviewed_view", "type": "V"}],
                         [{"name": row["name"], "type": "P"} for row in self.objects()]):
            with self.subTest(modified=modified[-1]["name"]), self.assertRaises(RuntimeError):
                retire.validate_scope(modified)

    def test_schema_binding_is_rejected(self):
        objects = self.objects()
        objects[0]["is_schema_bound"] = 1
        with self.assertRaisesRegex(RuntimeError, "schema bound"):
            retire.validate_scope(objects)

    def test_only_constraints_owned_by_retired_tables_are_internal(self):
        reviewed_ids = {100, 200}
        table_ids = {100}
        internal_check = {"referencing_id": 300, "referencing_type": "C", "referencing_parent_object_id": 100}
        self.assertTrue(retire.dependency_is_internal(internal_check, reviewed_ids, table_ids))
        for kind in ("C", "D", "F", "PK", "UQ"):
            external_constraint = dict(internal_check, referencing_type=kind, referencing_parent_object_id=999)
            with self.subTest(kind=kind):
                self.assertFalse(retire.dependency_is_internal(external_constraint, reviewed_ids, table_ids))
        self.assertFalse(retire.dependency_is_internal(dict(internal_check, referencing_type="TR"), reviewed_ids, table_ids))
        self.assertFalse(retire.dependency_is_internal(dict(internal_check, referencing_type="V"), reviewed_ids, table_ids))

    def test_modified_plan_and_wrong_source_are_rejected(self):
        catalogue = {"identity": {"database_name": retire.DATABASE, "machine": retire.MACHINE}, "objects": self.objects()}
        plan = {"format": 1, "created_at": "test", "catalogue": catalogue, "catalogue_sha256": retire.digest(catalogue)}
        retire.validate_plan(plan, plan["catalogue_sha256"])
        original = plan["catalogue_sha256"]
        plan["catalogue"]["objects"].pop()
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            retire.validate_plan(plan, original)
        catalogue["identity"]["machine"] = "wrong-host"
        plan["catalogue_sha256"] = retire.digest(catalogue)
        with self.assertRaisesRegex(RuntimeError, "different SQL source"):
            retire.validate_plan(plan, plan["catalogue_sha256"])

    def test_backup_must_be_verified_and_still_match_its_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.checkpoint(folder)
            path = Path(receipt["sql_source"]["backup_path"])
            retire.verify_backup_receipt(receipt)
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                retire.verify_backup_receipt(receipt)
            receipt["sql_source"]["restore_verified"] = False
            with self.assertRaisesRegex(RuntimeError, "restore verification"):
                retire.verify_backup_receipt(receipt)

    def test_complete_receipt_is_optional_but_full_offsite_proof_is_required(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.checkpoint(folder)
            directory = Path(receipt["recovery_directory"])
            self.assertFalse((directory / "COMPLETE.json").exists())
            retire.verify_backup_receipt(receipt)
            (directory / "offsite_receipt.json").unlink()
            with self.assertRaisesRegex(RuntimeError, "artifact is missing"):
                retire.verify_backup_receipt(receipt)

    def test_failed_offsite_or_postgres_proof_blocks_retirement(self):
        for name, field, value, error in (("offsite_receipt.json", "upload_download_decrypt_sha256", "failed", "offsite"),
                                          ("restore-verification.json", "postgres", {"status": "failed"}, "PostgreSQL")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                receipt = self.checkpoint(folder)
                path = Path(receipt["recovery_directory"]) / name
                proof = json.loads(path.read_text())
                proof[field] = value
                path.write_text(json.dumps(proof))
                with self.assertRaisesRegex(RuntimeError, error):
                    retire.verify_backup_receipt(receipt)

    def test_missing_target_or_wrong_restore_artifact_blocks_retirement(self):
        for mode in ("missing", "wrong-hash"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                receipt = self.checkpoint(folder)
                path = Path(receipt["recovery_directory"]) / "restore-verification.json"
                proof = json.loads(path.read_text())
                if mode == "missing":
                    proof["sql"]["databases"].pop()
                else:
                    proof["sql"]["databases"][0]["backup_sha256"] = "0" * 64
                path.write_text(json.dumps(proof))
                with self.assertRaisesRegex(RuntimeError, "three captured/restored|does not match"):
                    retire.verify_backup_receipt(receipt)

    def test_equivalent_backup_outside_capture_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.checkpoint(folder)
            original = Path(receipt["sql_source"]["backup_path"])
            replacement = Path(folder) / "equivalent.bak"
            replacement.write_bytes(original.read_bytes())
            receipt["sql_source"]["backup_path"] = str(replacement)
            with self.assertRaisesRegex(RuntimeError, "exact captured"):
                retire.verify_backup_receipt(receipt)

    def test_missing_checkpoint_and_changed_capture_manifest_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.checkpoint(folder)
            receipt.pop("recovery_directory")
            with self.assertRaisesRegex(RuntimeError, "checkpoint directory"):
                retire.verify_backup_receipt(receipt)
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.checkpoint(folder)
            path = Path(receipt["recovery_directory"]) / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["added"] = "changed"
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(RuntimeError, "encryption manifest"):
                retire.verify_backup_receipt(receipt)

    def test_future_retired_checkpoint_is_general_recovery_only(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.retired_checkpoint(folder)
            retire.verify_backup_receipt(receipt, require_legacy_rehearsal=False)
            with self.assertRaisesRegex(RuntimeError, "retirement rehearsal has not passed"):
                retire.verify_backup_receipt(receipt)

    def test_retired_checkpoint_requires_matching_captured_marker_and_config(self):
        for options, error in (({"parallel": True}, "disable parallel"),
                               ({"machine": "other-host"}, "marker does not match"),
                               ({"remaining": True}, "empty removal-scope")):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as folder:
                receipt = self.retired_checkpoint(folder, **options)
                with self.assertRaisesRegex(RuntimeError, error):
                    retire.verify_backup_receipt(receipt, require_legacy_rehearsal=False)

    def test_missing_captured_marker_cannot_explain_an_empty_sql_catalogue(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt = self.retired_checkpoint(folder)
            directory = Path(receipt["recovery_directory"])
            with tarfile.open(directory / "deployment-config.tar.gz", "w:gz"):
                pass
            with self.assertRaisesRegex(RuntimeError, "captured retirement marker"):
                retire.captured_retirement_marker(directory)

    def test_catalogue_drift_prevents_every_drop(self):
        query = unittest.mock.Mock()
        staging = types.ModuleType("reporting_mirror.sql_staging")
        staging.query = query
        mssql = types.ModuleType("backups.services.mssql")
        mssql.quote_identifier = lambda value: "[" + value.replace("]", "]]") + "]"
        with patch.dict("sys.modules", {"reporting_mirror.sql_staging": staging,
                                        "backups.services.mssql": mssql}), \
                patch.object(retire, "catalogue", return_value={"changed": True}):
            with self.assertRaisesRegex(RuntimeError, "changed after plan review"):
                retire.apply_plan(object(), {"catalogue_sha256": "previous-catalogue"})
        query.assert_not_called()

    def test_empty_redis_queue_with_no_backing_list_is_accepted(self):
        for count in (0, 1):
            with self.subTest(message_count=count):
                app = unittest.mock.MagicMock()
                inspector = app.control.inspect.return_value
                inspector.active.return_value = {"reporting-worker": []}
                inspector.reserved.return_value = {"reporting-worker": []}
                inspector.scheduled.return_value = {"reporting-worker": []}
                channel = app.connection_for_read.return_value.__enter__.return_value.channel.return_value

                def declare(*, queue, passive, durable):
                    if passive:
                        raise RuntimeError("Redis404: no queue backing list")
                    self.assertEqual(queue, "p6_reporting")
                    self.assertTrue(durable)
                    return types.SimpleNamespace(message_count=count)

                channel.queue_declare.side_effect = declare
                celery = types.ModuleType("p6_backup_admin.celery")
                celery.app = app
                with patch.dict("sys.modules", {"p6_backup_admin.celery": celery}):
                    if count:
                        with self.assertRaisesRegex(RuntimeError, "queue is not empty"):
                            retire.celery_gate()
                    else:
                        retire.celery_gate()
                channel.close.assert_called_once()

    def test_successful_drop_is_confined_to_the_reviewed_schema(self):
        protected = [{"object_id": 1, "name": "TASK", "type": "U"}]
        current = {"table_rows": {"xertoolkit_result_project_metrics": 5, "settings_retained": 2},
                   "protected_dbo_catalogue_sha256": retire.digest(protected)}
        statements = []

        def query(connection, statement, parameters=()):
            statements.append(statement)
            if statement.startswith("DROP "):
                return []
            if "o.is_ms_shipped=0" in statement:
                return protected
            if statement.startswith("SELECT COUNT_BIG(*)"):
                return [{"n": 2}]
            if statement.startswith("SELECT t.name"):
                return [{"name": "settings_retained"}]
            return []

        staging = types.ModuleType("reporting_mirror.sql_staging")
        staging.query = query
        mssql = types.ModuleType("backups.services.mssql")
        mssql.quote_identifier = lambda value: "[" + value.replace("]", "]]") + "]"
        with patch.dict("sys.modules", {"reporting_mirror.sql_staging": staging,
                                        "backups.services.mssql": mssql}), \
                patch.object(retire, "catalogue", return_value=current):
            result = retire.apply_plan(object(), {"catalogue_sha256": retire.digest(current)})
        drops = [statement for statement in statements if statement.startswith("DROP ")]
        self.assertEqual(len(drops), 40)
        self.assertTrue(all(" [powerbitables].[xertoolkit_" in statement for statement in drops))
        self.assertEqual(result["retained_table_rows"], {"settings_retained": 2})
        self.assertTrue(drops[0].startswith("DROP TRIGGER"))
        self.assertTrue(drops[-1].startswith("DROP TABLE"))


if __name__ == "__main__":
    unittest.main()
