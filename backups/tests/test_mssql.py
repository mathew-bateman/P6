from __future__ import annotations

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from backups.services.mssql import (
    MoveSpec,
    build_backup_sql,
    build_restore_moves,
    build_restore_sql,
    execute_statement,
    quote_identifier,
    sql_string,
)


class DummyTarget:
    sql_database = "Axial"
    resolved_data_file_path = "/var/database/Axial_DAT.MDF"
    resolved_log_file_path = "/var/database/Axial_LOG.LDF"


class MssqlSqlTests(SimpleTestCase):
    def test_quote_identifier_escapes_closing_bracket(self):
        self.assertEqual(quote_identifier("Bad]Name"), "[Bad]]Name]")

    def test_sql_string_escapes_single_quote(self):
        self.assertEqual(sql_string("/var/backups/O'Brien.bak"), "N'/var/backups/O''Brien.bak'")

    def test_build_backup_sql_uses_native_sql_server_backup(self):
        sql = build_backup_sql(
            database_name="Axial",
            backup_path="/var/backups/axialp6_backup_20260428_120000.bak",
            compression=True,
        )
        self.assertIn("BACKUP DATABASE [Axial]", sql)
        self.assertIn("TO DISK = N'/var/backups/axialp6_backup_20260428_120000.bak'", sql)
        self.assertIn("CHECKSUM", sql)
        self.assertIn("COMPRESSION", sql)

    def test_build_restore_sql_includes_move_specs(self):
        sql = build_restore_sql(
            database_name="Axial",
            backup_path="/var/backups/input.bak",
            moves=[
                MoveSpec("Axial_DAT", "/var/database/Axial_DAT.MDF"),
                MoveSpec("Axial_LOG", "/var/database/Axial_LOG.LDF"),
            ],
        )
        self.assertIn("RESTORE DATABASE [Axial]", sql)
        self.assertIn("MOVE N'Axial_DAT' TO N'/var/database/Axial_DAT.MDF'", sql)
        self.assertIn("MOVE N'Axial_LOG' TO N'/var/database/Axial_LOG.LDF'", sql)

    def test_build_restore_moves_uses_target_paths(self):
        moves = build_restore_moves(
            DummyTarget(),
            [
                {"LogicalName": "SourceData", "Type": "D"},
                {"LogicalName": "SourceLog", "Type": "L"},
            ],
        )
        self.assertEqual(
            moves,
            [
                MoveSpec("SourceData", "/var/database/Axial_DAT.MDF"),
                MoveSpec("SourceLog", "/var/database/Axial_LOG.LDF"),
            ],
        )

    @patch("backups.services.mssql.connect")
    def test_execute_statement_parameterizes_and_commits_transaction(self, connect):
        target = DummyTarget()
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.nextset.return_value = False
        connect.return_value = connection

        execute_statement(
            target,
            "EXEC save_config @id = ?;",
            parameters=(11,),
            database="Axial",
            transactional=True,
        )

        connect.assert_called_once_with(
            target,
            database="Axial",
            autocommit=False,
        )
        cursor.execute.assert_called_once_with(
            "EXEC save_config @id = ?;",
            (11,),
        )
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        connection.close.assert_called_once_with()

    @patch("backups.services.mssql.connect")
    def test_execute_statement_rolls_back_failed_transaction(self, connect):
        connection = Mock()
        connection.cursor.return_value.execute.side_effect = RuntimeError("failed")
        connect.return_value = connection

        with self.assertRaisesRegex(RuntimeError, "failed"):
            execute_statement(
                DummyTarget(),
                "EXEC save_config @id = ?;",
                parameters=(11,),
                transactional=True,
            )

        connection.rollback.assert_called_once_with()
        connection.commit.assert_not_called()
        connection.close.assert_called_once_with()
