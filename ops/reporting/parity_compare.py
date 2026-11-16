"""Streaming, value-preserving comparison of SQL and PostgreSQL report rows.

``compare_cursor_rows`` consumes each iterator once and returns whole-view and
per-project multiset digests. Row order is immaterial; duplicate multiplicity
is not. Only the named runtime columns are omitted by default.

If a comparison differs, rerun queries restricted to its affected project IDs
and pass those iterators to ``difference_samples``. That second pass spills
row multiplicities to a temporary SQLite file, giving deterministic *actual*
differences without retaining the dataset in memory. It never opens either
source database and deletes its scratch file on return.
"""
from collections.abc import Mapping
from datetime import date, datetime, time, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import uuid


RUNTIME_COLUMNS = frozenset({"check_run_id", "refreshed_at", "calculated_date"})
JSON_COLUMNS = frozenset({"detail_json"})
_MODULUS = 1 << 256


def _decimal_text(value):
    """Canonical numeric value without Decimal context rounding or float casts."""
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        return str(number)
    sign, digits, exponent = number.as_tuple()
    digits = list(digits)
    if not any(digits):
        return "0"
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    coefficient = ("-" if sign else "") + "".join(map(str, digits))
    return coefficient if exponent == 0 else coefficient + "e" + str(exponent)


class _JSONObject(list):
    """Retain duplicate JSON keys, whose order must not be normalised away."""


def _json_value(value):
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, (Decimal, int, float)):
        return ["number", _decimal_text(value)]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, _JSONObject):
        names = [key for key, _ in value]
        if len(set(names)) != len(names):
            return ["object-with-duplicate-keys", [[key, _json_value(item)] for key, item in value]]
        return ["object", [[key, _json_value(item)] for key, item in sorted(value)]]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings.")
        return ["object", [[key, _json_value(value[key])] for key in sorted(value)]]
    if isinstance(value, (list, tuple)):
        return ["array", [_json_value(item) for item in value]]
    raise TypeError("Unsupported JSON value: " + type(value).__name__)


def _reject_json_constant(value):
    raise ValueError("Non-standard JSON constant: " + value)


def canonical_value(value, column):
    """Represent one typed value without masking text, date or NULL changes."""
    if value is None:
        return None
    name = column["name"]
    kind = column.get("type", column.get("data_type", "")).lower().strip()
    if name in JSON_COLUMNS or kind in {"json", "jsonb"}:
        if value == "":
            return ""
        parsed = json.loads(value, parse_int=Decimal, parse_float=Decimal,
                            parse_constant=_reject_json_constant,
                            object_pairs_hook=_JSONObject) if isinstance(value, str) else value
        return {"$json": _json_value(parsed)}
    numeric = kind.startswith(("numeric", "decimal")) or kind in {
        "int", "integer", "bigint", "smallint", "tinyint", "bit", "boolean",
        "double precision", "float", "real", "money", "smallmoney",
    }
    if numeric:
        return {"$number": _decimal_text(int(value) if isinstance(value, bool) else value)}
    if isinstance(value, datetime):
        if kind in {"date"} and value.time() == time.min:
            return {"$date": value.date().isoformat()}
        if kind in {"timestamp with time zone", "timestamptz", "datetimeoffset"} and value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, time):
        return {"$time": value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$binary": bytes(value).hex()}
    if kind in {"uuid", "uniqueidentifier"}:
        return {"$uuid": str(uuid.UUID(str(value)))}
    if isinstance(value, Decimal):
        return {"$decimal": _decimal_text(value)}
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return {"$uuid": str(value)}
    raise TypeError("Unsupported comparison value for " + name + ": " + type(value).__name__)


def _layout(columns, ignored_runtime):
    columns = [dict(column) if isinstance(column, Mapping) else {"name": column} for column in columns]
    names = [column["name"] for column in columns]
    if not names or len(names) != len(set(names)):
        raise ValueError("Comparison columns must be nonempty and uniquely named.")
    ignored = RUNTIME_COLUMNS if ignored_runtime is None else frozenset(ignored_runtime)
    positions = [index for index, name in enumerate(names) if name not in ignored]
    if not positions:
        raise ValueError("At least one non-runtime comparison column is required.")
    return columns, positions, [names[index] for index in positions], [name for name in names if name in ignored]


def _values(row, columns, positions):
    if isinstance(row, Mapping):
        # Both SQL aliases and PostgreSQL contract names should be projected
        # explicitly. Missing values are errors, never silently treated as NULL.
        return [row[columns[index]["name"]] for index in positions]
    if len(row) == len(columns):
        return [row[index] for index in positions]
    if len(row) == len(positions):
        return list(row)
    raise ValueError("Cursor row does not match the full or runtime-excluded column projection.")


def _encoded_rows(rows, columns, positions):
    selected = [columns[index] for index in positions]
    project_index = next((index for index, column in enumerate(selected) if column["name"] == "proj_id"), None)
    for row in rows:
        values = _values(row, columns, positions)
        canonical = [canonical_value(value, column) for value, column in zip(values, selected)]
        encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        project = values[project_index] if project_index is not None else None
        if project is not None and project_index is not None:
            project_kind = selected[project_index].get("type", selected[project_index].get("data_type", "")).lower()
            if project_kind in {"int", "integer", "bigint", "smallint", "tinyint"} or isinstance(project, Decimal):
                number = project if isinstance(project, Decimal) else Decimal(str(project))
                project = int(number) if number.is_finite() and number == number.to_integral_value() else str(number)
        yield encoded, project


class _MultisetDigest:
    def __init__(self):
        self.count = 0
        self.total = 0
        self.squared_total = 0

    def add(self, digest):
        number = int.from_bytes(digest, "big")
        self.count += 1
        self.total = (self.total + number) % _MODULUS
        self.squared_total = (self.squared_total + number * number) % _MODULUS

    def value(self):
        summary = str(self.count) + ":" + format(self.total, "064x") + ":" + format(self.squared_total, "064x")
        return hashlib.sha256(summary.encode("ascii")).hexdigest()


def compare_cursor_rows(sql_rows_iterator, pg_rows_iterator, columns, ignored_runtime=None):
    """Compare every supplied row and return JSON-serialisable parity evidence.

    ``columns`` is an ordered list of ``{"name": ..., "type": ...}`` contracts.
    Iterators can yield dictionaries or sequences. Sequences may include the
    full contract or only its runtime-excluded columns, matching either SELECT.
    Memory is O(number of distinct proj_id values), not O(number of rows).
    """
    columns, positions, selected, excluded = _layout(columns, ignored_runtime)
    totals = [_MultisetDigest(), _MultisetDigest()]
    groups = [{}, {}]
    has_project = "proj_id" in selected
    for side, rows in enumerate((sql_rows_iterator, pg_rows_iterator)):
        for encoded, project in _encoded_rows(rows, columns, positions):
            digest = hashlib.sha256(encoded).digest()
            totals[side].add(digest)
            if has_project:
                groups[side].setdefault(project, _MultisetDigest()).add(digest)
    projects = []
    for project in sorted(set(groups[0]) | set(groups[1]), key=lambda item: (item is None, str(item))):
        values = [group.get(project, _MultisetDigest()) for group in groups]
        counts, digests = [item.count for item in values], [item.value() for item in values]
        projects.append({"proj_id": project, "rows": counts, "digests": digests,
                         "matched": counts[0] == counts[1] and digests[0] == digests[1]})
    counts, digests = [item.count for item in totals], [item.value() for item in totals]
    return {
        "matched": counts[0] == counts[1] and digests[0] == digests[1],
        "rows": counts, "digests": digests, "columns": selected,
        "excluded_columns": excluded, "project_groups": projects,
        "affected_project_ids": [item["proj_id"] for item in projects if not item["matched"]],
        "normalisation": "typed numeric values; JSON object order/number syntax; no text case, whitespace or array-order changes",
        "digest_method": "SHA-256 of row count and modular sums of SHA-256 row hashes and their squares",
    }


def difference_samples(sql_rows_iterator, pg_rows_iterator, columns, ignored_runtime=None,
                       *, limit=20, scratch_directory=None):
    """Return exact, deterministic row-multiplicity differences using disk.

    Call with replayed source iterators, preferably restricted to affected
    projects. This exhausts both iterators: ``limit`` limits returned samples,
    not rows inspected. The SQLite scratch database is removed on completion
    or error. Neither source database is modified.
    """
    if limit < 1:
        raise ValueError("Difference sample limit must be positive.")
    columns, positions, selected, _ = _layout(columns, ignored_runtime)
    if scratch_directory is not None:
        Path(scratch_directory).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p6-parity-", dir=scratch_directory) as directory:
        database = sqlite3.connect(str(Path(directory) / "rows.sqlite3"))
        try:
            database.execute("PRAGMA cache_size=-4096")
            database.execute("PRAGMA temp_store=FILE")
            database.execute("CREATE TABLE rows (digest BLOB NOT NULL, payload TEXT NOT NULL, sql_count INTEGER NOT NULL, pg_count INTEGER NOT NULL, PRIMARY KEY(digest,payload)) WITHOUT ROWID")
            counts = []
            for side, rows in enumerate((sql_rows_iterator, pg_rows_iterator)):
                count = 0
                statement = ("INSERT INTO rows VALUES (?,?,?,?) ON CONFLICT(digest,payload) DO UPDATE SET "
                             + ("sql_count=sql_count+1" if side == 0 else "pg_count=pg_count+1"))
                batch = []
                for encoded, _ in _encoded_rows(rows, columns, positions):
                    batch.append((hashlib.sha256(encoded).digest(), encoded.decode("utf-8"), int(side == 0), int(side == 1)))
                    count += 1
                    if len(batch) == 2000:
                        database.executemany(statement, batch)
                        batch.clear()
                if batch:
                    database.executemany(statement, batch)
                counts.append(count)
            database.commit()
            differing = database.execute("SELECT count(*) FROM rows WHERE sql_count<>pg_count").fetchone()[0]
            samples = []
            for digest, payload, sql_count, pg_count in database.execute(
                    "SELECT digest,payload,sql_count,pg_count FROM rows WHERE sql_count<>pg_count ORDER BY digest,payload LIMIT ?", (limit,)):
                samples.append({"row": dict(zip(selected, json.loads(payload))),
                                "sql_count": sql_count, "postgres_count": pg_count,
                                "row_digest": bytes(digest).hex()})
            return {"rows": counts, "different_row_values": differing,
                    "samples": samples, "samples_truncated": differing > limit}
        finally:
            database.close()
