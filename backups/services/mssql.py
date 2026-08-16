from __future__ import annotations

import pyodbc

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class MoveSpec:
    logical_name: str
    destination_path: str


def quote_identifier(identifier: str) -> str:
    return f"[{identifier.replace(']', ']]')}]"


def sql_string(value: str) -> str:
    return "N'" + value.replace("'", "''") + "'"


def build_backup_sql(*, database_name: str, backup_path: str, compression: bool = False) -> str:
    options = ["INIT", "CHECKSUM"]
    if compression:
        options.append("COMPRESSION")
    return (
        f"BACKUP DATABASE {quote_identifier(database_name)} "
        f"TO DISK = {sql_string(backup_path)} "
        f"WITH {', '.join(options)};"
    )


def build_verifyonly_sql(*, backup_path: str) -> str:
    return f"RESTORE VERIFYONLY FROM DISK = {sql_string(backup_path)} WITH CHECKSUM;"


def build_filelistonly_sql(*, backup_path: str) -> str:
    return f"RESTORE FILELISTONLY FROM DISK = {sql_string(backup_path)};"


def build_restore_sql(
    *,
    database_name: str,
    backup_path: str,
    moves: list[MoveSpec],
    replace: bool = True,
) -> str:
    options = ["CHECKSUM", "RECOVERY"]
    if replace:
        options.append("REPLACE")
    for move in moves:
        options.append(
            f"MOVE {sql_string(move.logical_name)} TO {sql_string(move.destination_path)}"
        )
    return (
        f"RESTORE DATABASE {quote_identifier(database_name)} "
        f"FROM DISK = {sql_string(backup_path)} "
        f"WITH {', '.join(options)};"
    )


def build_single_user_sql(database_name: str) -> str:
    return (
        f"ALTER DATABASE {quote_identifier(database_name)} "
        "SET SINGLE_USER WITH ROLLBACK IMMEDIATE;"
    )


def build_multi_user_sql(database_name: str) -> str:
    return f"ALTER DATABASE {quote_identifier(database_name)} SET MULTI_USER;"


def build_connection_string(target, *, database: str = "master") -> str:
    password = target.sql_password
    if not password:
        raise RuntimeError(
            "SQL password is blank. Enter the SQL password on the target."
        )

    parts = [
        f"DRIVER={{{target.odbc_driver}}}",
        f"SERVER={target.sql_host},{target.sql_port}",
        f"DATABASE={database}",
        f"UID={target.sql_username}",
        f"PWD={password}",
        f"Encrypt={'yes' if target.encrypt_connection else 'no'}",
        f"TrustServerCertificate={'yes' if target.trust_server_certificate else 'no'}",
    ]
    return ";".join(parts)


def connect(target, *, database: str = "master", autocommit: bool = True):

    return pyodbc.connect(
        build_connection_string(target, database=database),
        autocommit=autocommit,
        timeout=30,
    )


def _execute(cursor, sql: str, parameters: Sequence[object] | None = None) -> None:
    try:
        if parameters:
            cursor.execute(sql, tuple(parameters))
        else:
            cursor.execute(sql)
    except pyodbc.Error as e:
        if len(e.args) > 0 and str(e.args[0]) in ('01003', '01000'):
            pass
        else:
            raise


def execute_statement(
    target,
    sql: str,
    *,
    parameters: Sequence[object] | None = None,
    database: str = "master",
    transactional: bool = False,
) -> None:
    connection = connect(target, database=database, autocommit=not transactional)
    try:
        cursor = connection.cursor()
        _execute(cursor, sql, parameters)
        while cursor.nextset():
            pass
        if transactional:
            connection.commit()
    except Exception:
        if transactional:
            connection.rollback()
        raise
    finally:
        connection.close()


def fetch_result_sets(
    target,
    sql: str,
    *,
    parameters: Sequence[object] | None = None,
    database: str = "master",
    transactional: bool = False,
) -> list[list[dict[str, object]]]:
    connection = connect(target, database=database, autocommit=not transactional)
    try:
        cursor = connection.cursor()
        _execute(cursor, sql, parameters)
        result_sets: list[list[dict[str, object]]] = []
        while True:
            if cursor.description:
                columns = [column[0] for column in cursor.description]
                result_sets.append(
                    [dict(zip(columns, row)) for row in cursor.fetchall()]
                )
            if not cursor.nextset():
                break
        if transactional:
            connection.commit()
        return result_sets
    except Exception:
        if transactional:
            connection.rollback()
        raise
    finally:
        connection.close()


def fetch_rows(
    target,
    sql: str,
    *,
    parameters: Sequence[object] | None = None,
    database: str = "master",
    transactional: bool = False,
) -> list[dict[str, object]]:
    result_sets = fetch_result_sets(
        target,
        sql,
        parameters=parameters,
        database=database,
        transactional=transactional,
    )
    return result_sets[0] if result_sets else []


def backup_database(target, *, backup_filename: str) -> str:
    backup_path = f"{target.sql_backup_dir.rstrip('/')}/{backup_filename}"
    execute_statement(
        target,
        build_backup_sql(
            database_name=target.sql_database,
            backup_path=backup_path,
            compression=target.use_compression,
        ),
    )
    execute_statement(target, build_verifyonly_sql(backup_path=backup_path))
    return backup_path


def fetch_restore_filelist(target, *, backup_path: str) -> list[dict[str, object]]:
    return fetch_rows(target, build_filelistonly_sql(backup_path=backup_path))


def build_restore_moves(target, filelist_rows: list[dict[str, object]]) -> list[MoveSpec]:
    data_rows = [row for row in filelist_rows if str(row.get("Type", "")).upper() == "D"]
    log_rows = [row for row in filelist_rows if str(row.get("Type", "")).upper() == "L"]
    moves: list[MoveSpec] = []

    for index, row in enumerate(data_rows):
        logical_name = str(row.get("LogicalName") or "").strip()
        if not logical_name:
            continue
        destination = target.resolved_data_file_path
        if len(data_rows) > 1:
            stem, suffix = destination.rsplit(".", 1) if "." in destination else (destination, "ndf")
            destination = f"{stem}_{index + 1}.{suffix}"
        moves.append(MoveSpec(logical_name=logical_name, destination_path=destination))

    for index, row in enumerate(log_rows):
        logical_name = str(row.get("LogicalName") or "").strip()
        if not logical_name:
            continue
        destination = target.resolved_log_file_path
        if len(log_rows) > 1:
            stem, suffix = destination.rsplit(".", 1) if "." in destination else (destination, "ldf")
            destination = f"{stem}_{index + 1}.{suffix}"
        moves.append(MoveSpec(logical_name=logical_name, destination_path=destination))

    if not moves:
        raise RuntimeError("RESTORE FILELISTONLY returned no movable database files.")
    return moves


def restore_database(target, *, backup_filename: str) -> None:
    backup_path = f"{target.sql_backup_dir.rstrip('/')}/{backup_filename}"
    execute_statement(target, build_verifyonly_sql(backup_path=backup_path))
    moves = build_restore_moves(target, fetch_restore_filelist(target, backup_path=backup_path))

    try:
        execute_statement(target, build_single_user_sql(target.sql_database))
        execute_statement(
            target,
            build_restore_sql(
                database_name=target.sql_database,
                backup_path=backup_path,
                moves=moves,
                replace=True,
            ),
        )
    finally:
        execute_statement(target, build_multi_user_sql(target.sql_database))
