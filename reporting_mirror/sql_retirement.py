"""Persistent guard for the explicitly retired P6 22 SQL report layer."""
import json
from pathlib import Path

MARKER_PATH = Path("/stage/state/sql-reporting-retired.json")
MESSAGE = "SQL reporting has been retired. PostgreSQL continues serving reports. Restore the verified SQL recovery backup before enabling SQL calculations."


def retired(*, marker_path=MARKER_PATH):
    """An absent marker preserves recovery-host behaviour; damaged state fails closed."""
    try:
        payload = json.loads(Path(marker_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as error:
        raise RuntimeError("The SQL reporting retirement marker could not be read safely.") from error
    if not isinstance(payload, dict):
        raise RuntimeError("The SQL reporting retirement marker is invalid.")
    target = payload.get("target") or payload.get("sql_source") or {}
    return payload.get("status") == "retired" and isinstance(target, dict) and target.get("database") == "P62212_1"


def require_sql_reporting():
    if retired():
        raise RuntimeError(MESSAGE)
