import json
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings


def secrets_config():
    return json.loads(Path(settings.P6_REPORTING_CONFIG_FILE).read_text())


def reporting_available():
    return Path(settings.P6_REPORTING_CONFIG_FILE).is_file()


def postgres_connection(*, reader=False, autocommit=False):
    import psycopg
    from psycopg.rows import dict_row
    config = secrets_config()["postgres"]
    return psycopg.connect(
        host=config["host"], port=config["port"], dbname=config["dbname"],
        user=config["reader_user"] if reader else config["user"],
        password=config["reader_password"] if reader else config["password"],
        connect_timeout=10, autocommit=autocommit, row_factory=dict_row,
        options="-cstatement_timeout=60000" if reader else "-cstatement_timeout=900000",
        application_name="P6BackupAdmin reporting reader" if reader else "P6BackupAdmin reporting sync",
    )


def staging_target():
    config = secrets_config()["staging"]
    if config["host"] != "p6-reporting-sql-stage":
        raise RuntimeError("Reporting restores must use the dedicated SQL staging container.")
    return SimpleNamespace(
        sql_host=config["host"], sql_port=config["port"], sql_username=config["username"],
        sql_password=config["password"], odbc_driver="ODBC Driver 18 for SQL Server",
        encrypt_connection=True, trust_server_certificate=True,
    )
