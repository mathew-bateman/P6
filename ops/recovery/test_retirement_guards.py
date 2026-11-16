"""Focused safety checks; no production database or service is used."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("install_retirement_guards", Path(__file__).with_name("install_retirement_guards.py"))
guards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guards)


class RetirementMarkerTests(unittest.TestCase):
    def setUp(self):
        namespace = {}
        exec(compile(guards.HELPER, "sql_retirement.py", "exec"), namespace)
        self.retired = namespace["retired"]
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.marker = Path(self.temporary.name) / "marker.json"

    def write(self, payload):
        self.marker.write_text(json.dumps(payload), encoding="utf-8")

    def test_absent_marker_keeps_recovery_host_sql_available(self):
        self.assertFalse(self.retired(marker_path=self.marker))

    def test_explicit_retirement_for_the_expected_database_is_required(self):
        for payload, expected in [
            ({"status": "retired", "target": {"database": "P62212_1"}}, True),
            ({"status": "retired", "sql_source": {"database": "P62212_1"}}, True),
            ({"status": "planned", "target": {"database": "P62212_1"}}, False),
            ({"status": "retired", "target": {"database": "Axial"}}, False),
            ({"status": "retired"}, False),
        ]:
            with self.subTest(payload=payload):
                self.write(payload)
                self.assertEqual(self.retired(marker_path=self.marker), expected)

    def test_corrupt_state_fails_closed(self):
        for content in ["{broken", "[]"]:
            self.marker.write_text(content, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                self.retired(marker_path=self.marker)


class GuardDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "reporting_mirror").mkdir()
        self.path = self.root / "reporting_mirror" / "forms.py"
        self.path.write_bytes(b"original\n")
        self.before = {"reporting_mirror/forms.py": b"original\n"}
        self.after = {"reporting_mirror/forms.py": b"changed\n", "reporting_mirror/sql_retirement.py": b"new helper\n"}
        self.backup = self.root / "rollback"

    def test_unknown_source_is_rejected_before_backup_or_mutation(self):
        with self.assertRaisesRegex(ValueError, "Production source changed"):
            guards.install(self.root, self.backup, apply=True)
        self.assertEqual(self.path.read_bytes(), b"original\n")
        self.assertFalse(self.backup.exists())

    def test_preview_has_no_filesystem_side_effects(self):
        with patch.object(guards, "candidates", return_value=(self.before, self.after)):
            result = guards.install(self.root, self.backup, apply=False)
        self.assertEqual(result["status"], "preview")
        self.assertEqual(self.path.read_bytes(), b"original\n")
        self.assertFalse(self.backup.exists())
        self.assertFalse((self.path.parent / "sql_retirement.py").exists())

    def test_partial_deployment_restores_original_files(self):
        real_write = guards.atomic_write

        def fail_for_new_helper(path, data, mode):
            if path.name == "sql_retirement.py":
                raise OSError("simulated disk failure")
            return real_write(path, data, mode)

        with patch.object(guards, "candidates", return_value=(self.before, self.after)), patch.object(guards, "atomic_write", side_effect=fail_for_new_helper):
            with self.assertRaisesRegex(OSError, "simulated disk failure"):
                guards.install(self.root, self.backup, apply=True)
        self.assertEqual(self.path.read_bytes(), b"original\n")
        self.assertEqual((self.backup / "reporting_mirror/forms.py").read_bytes(), b"original\n")
        self.assertFalse((self.path.parent / "sql_retirement.py").exists())

    def test_intervening_edit_is_preserved(self):
        def capture_then_edit(root):
            self.path.write_bytes(b"concurrent edit\n")
            return self.before, self.after

        with patch.object(guards, "candidates", side_effect=capture_then_edit):
            with self.assertRaisesRegex(RuntimeError, "changed during deployment"):
                guards.install(self.root, self.backup, apply=True)
        self.assertEqual(self.path.read_bytes(), b"concurrent edit\n")

    def test_existing_rollback_directory_is_never_overwritten(self):
        self.backup.mkdir()
        sentinel = self.backup / "existing"
        sentinel.write_text("preserve", encoding="utf-8")
        with patch.object(guards, "candidates", return_value=(self.before, self.after)):
            with self.assertRaises(FileExistsError):
                guards.install(self.root, self.backup, apply=True)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        self.assertEqual(self.path.read_bytes(), b"original\n")

    def test_engine_revision_inputs_are_excluded(self):
        revision_inputs = {"native_pipeline.py", "native_engine.py", "native_evidence.py", "native_settings.py", "native_fixtures.py", "native_validation.py"}
        self.assertTrue({Path(name).name for name in guards.PREIMAGES}.isdisjoint(revision_inputs))


if __name__ == "__main__":
    unittest.main()
