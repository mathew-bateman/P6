#!/usr/bin/env python3
"""Install reviewed guards around the retired SQL reporting engine.

Run on the production host. The default is a non-mutating preview. Existing
source must match the reviewed SHA-256 preimages exactly. No calculation-engine
revision input is changed, so the existing PostgreSQL parity proof stays valid.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


PREIMAGES = {
    "reporting_mirror/forms.py": "38413cd43fa26a0019e30c58649490ea82e30d6e05b33b93ee4d29a99bb2f56c",
    "reporting_mirror/sync.py": "baa2617881e23c363ff8f2d7808b6aa3e1aa2af64dde3756a56c15e5fe7c8416",
    "reporting_mirror/sql_parallel.py": "85408ce95282893918724f6f9ba1f9446c0f7e893d4a2f9bfe817005c5830fb1",
    "reporting_mirror/views.py": "9a06995371885624633097e26fc416c72429c7e607e78967f6a1f993c0f91e56",
    "reporting_mirror/tasks.py": "450e613d1444c514667d2ddb696193e7a1441e703ed71fc9edfa11f8f65a8f3c",
    "templates/reporting_mirror/section_engine.html": "6eb8a8a42e30eb8caf52658cd426b2efc2dcd2e45adaa6a0efef81046f1517f9",
}

HELPER = '''"""Persistent guard for the explicitly retired P6 22 SQL report layer."""
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
'''


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace_once(source: str, old: str, new: str, *, filename: str) -> str:
    if source.count(old) != 1:
        raise ValueError(f"Expected one reviewed patch anchor in {filename}.")
    return source.replace(old, new, 1)


def transform(name: str, source: str) -> str:
    name = Path(name).name
    def patch(old, new):
        nonlocal source
        source = replace_once(source, old, new, filename=name)

    if name == "forms.py":
        patch("from reporting_mirror.connections import reporting_available\n", "from reporting_mirror.connections import reporting_available\nfrom reporting_mirror.sql_retirement import retired, MESSAGE\n")
        patch("    def clean_calculation_engine(self):\n", '''        if retired():
            self.fields["calculation_engine"].choices = [("postgres", "Native PostgreSQL")]
            self.fields["calculation_engine"].disabled = True
            self.fields["sql_parallel_enabled"].disabled = True
            self.initial.update(calculation_engine="postgres", sql_parallel_enabled=False)
            self.fields["calculation_engine"].help_text = MESSAGE
            self.fields["sql_parallel_enabled"].help_text = "The retired SQL reports are no longer refreshed."
            self.fields["serve_reports"].help_text = "PostgreSQL manages reporting rules and serves these reports."

    def clean_calculation_engine(self):
''')
        patch('        if engine != self.previous_engine and engine == "postgres":\n', '        if retired() and engine != "postgres":\n            raise forms.ValidationError(MESSAGE)\n        if engine != self.previous_engine and engine == "postgres":\n')
        patch('        interval = data.get("interval_minutes", 60)\n', '        if retired() and data.get("sql_parallel_enabled"):\n            self.add_error("sql_parallel_enabled", MESSAGE)\n        interval = data.get("interval_minutes", 60)\n')
    elif name == "sync.py":
        patch('    if selected_engine == "postgres":\n', '    if selected_engine == "sql":\n        from reporting_mirror.sql_retirement import require_sql_reporting\n        require_sql_reporting()\n    if selected_engine == "postgres":\n')
    elif name == "sql_parallel.py":
        patch('from reporting_mirror.sync import LOCK_ID\n', 'from reporting_mirror.sync import LOCK_ID\nfrom reporting_mirror.sql_retirement import retired\n')
        patch('    """Queue once per publication; broker failures cannot invalidate PG results."""\n', '    """Queue once per publication; broker failures cannot invalidate PG results."""\n    if retired():\n        return None\n')
        patch('    if not ReportingConfig.objects.filter(pk=1, sql_parallel_enabled=True, manage_settings_in_postgres=True).exists() or not reporting_available():\n        record.status = "skipped";', '    if retired() or not ReportingConfig.objects.filter(pk=1, sql_parallel_enabled=True, manage_settings_in_postgres=True).exists() or not reporting_available():\n        record.status = "skipped";')
    elif name == "views.py":
        patch('from reporting_mirror.connections import postgres_connection, reporting_available\n', 'from reporting_mirror.connections import postgres_connection, reporting_available\nfrom reporting_mirror.sql_retirement import retired\n')
        patch('        "config": config, "reporting_available": available,\n', '        "config": config, "reporting_available": available,\n        "sql_reporting_retired": retired(),\n')
        patch('class NativeEngineValidationView(ReportingStaffMixin, View):\n    def post(self, request):\n', 'class NativeEngineValidationView(ReportingStaffMixin, View):\n    def post(self, request):\n        from reporting_mirror.sql_retirement import retired, MESSAGE\n        if retired():\n            messages.warning(request, MESSAGE)\n            return redirect("reporting_mirror_dashboard")\n')
    elif name == "tasks.py":
        patch('def validate_native_engine(self, *, validation_id):\n    from reporting_mirror.native_validation import validate_native_engine as run_validation\n', '''def validate_native_engine(self, *, validation_id):
    from reporting_mirror.sql_retirement import retired, MESSAGE
    if retired():
        from django.utils import timezone
        from reporting_mirror.models import NativeEngineValidation
        NativeEngineValidation.objects.filter(pk=validation_id, status__in=["queued", "running"]).update(status="failed", progress="SQL engine comparison is unavailable after retirement", error_message=MESSAGE, finished_at=timezone.now())
        return MESSAGE
    from reporting_mirror.native_validation import validate_native_engine as run_validation
''')
    elif name == "section_engine.html":
        patch('{% if request.user.is_staff and reporting_available %}<form method="post" action="{% url \'reporting_mirror_validate_engine\' %}">', '{% if request.user.is_staff and reporting_available and not sql_reporting_retired %}<form method="post" action="{% url \'reporting_mirror_validate_engine\' %}">')
        patch('<div class="engine-story-title"><h2>P6 22 legacy reports</h2><span class="badge {% if config.sql_parallel_enabled %}success{% else %}warning{% endif %}">{% if config.sql_parallel_enabled %}SQL refresh enabled{% else %}SQL refresh paused{% endif %}</span></div>', '<div class="engine-story-title"><h2>P6 22 legacy reports</h2><span class="badge {% if sql_reporting_retired %}neutral{% elif config.sql_parallel_enabled %}success{% else %}warning{% endif %}">{% if sql_reporting_retired %}Retired{% elif config.sql_parallel_enabled %}SQL refresh enabled{% else %}SQL refresh paused{% endif %}</span></div>')
        patch('<p class="engine-story-intro">{% if config.sql_parallel_enabled %}The existing P6 22 SQL reports also refresh in parallel.{% else %}Refreshes for the existing P6 22 SQL reports are paused.{% endif %} P6 23 has no legacy SQL reporting installation.</p>', '<p class="engine-story-intro">{% if sql_reporting_retired %}SQL views and stored results were retired after a verified recovery backup. PostgreSQL serves current reports.{% elif config.sql_parallel_enabled %}The existing P6 22 SQL reports also refresh in parallel. P6 23 has no legacy SQL reporting installation.{% else %}Refreshes for the existing P6 22 SQL reports are paused. P6 23 has no legacy SQL reporting installation.{% endif %}</p>')
        patch('Latest SQL refresh ·', '{% if sql_reporting_retired %}Last archived SQL refresh{% else %}Latest SQL refresh{% endif %} ·')
        patch('<h2 id="engine-sql-refresh-title">Recent SQL refreshes</h2>', '<h2 id="engine-sql-refresh-title">{% if sql_reporting_retired %}Archived SQL refresh history{% else %}Recent SQL refreshes{% endif %}</h2>')
    else:
        raise ValueError(f"Unexpected patch target {name}.")
    if name.endswith(".py"):
        compile(source, name, "exec")
    return source


def candidates(app_root: Path) -> tuple[dict[str, bytes], dict[str, bytes]]:
    if (app_root / "reporting_mirror" / "sql_retirement.py").exists():
        raise ValueError("sql_retirement.py already exists; inspect before making another release.")
    before, after = {}, {}
    for name, expected in PREIMAGES.items():
        data = (app_root / name).read_bytes()
        if sha256(data) != expected:
            raise ValueError(f"Production source changed: {name}. Reconcile before deployment.")
        before[name] = data
        after[name] = transform(name, data.decode("utf-8")).encode("utf-8")
    compile(HELPER, "sql_retirement.py", "exec")
    after["reporting_mirror/sql_retirement.py"] = HELPER.encode("utf-8")
    return before, after


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.guards-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(app_root: Path, backup_root: Path, *, apply: bool) -> dict:
    app_root = app_root.resolve(strict=True)
    before, after = candidates(app_root)
    manifest = {
        "schema_version": 1,
        "status": "preview",
        "app_root": str(app_root),
        "files": [{"path": name, "before_sha256": sha256(before[name]) if name in before else None, "after_sha256": sha256(data)} for name, data in after.items()],
        "engine_revision_inputs_changed": False,
    }
    if not apply:
        return manifest
    # Finish and flush every rollback file before replacing any live source.
    backup_root.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(backup_root, 0o700)
    for name, data in before.items():
        (backup_root / name).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(app_root / name, backup_root / name)
    (backup_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    changed = []
    try:
        for name, data in after.items():
            path = app_root / name
            if name in before and path.read_bytes() != before[name]:
                raise RuntimeError(f"Production source changed during deployment: {name}.")
            if name not in before and path.exists():
                raise RuntimeError(f"Another task created {name} during deployment.")
            mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
            atomic_write(path, data, mode)
            changed.append(name)
    except BaseException:
        for name in reversed(changed):
            path = app_root / name
            if name in before:
                atomic_write(path, before[name], (backup_root / name).stat().st_mode & 0o777)
            else:
                path.unlink()
        raise
    manifest.update(status="installed", rollback_directory=str(backup_root))
    (backup_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, default=Path("/home/mat/Docker/P6-Standby/app"))
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    backup_root = args.backup_root or args.app_root / "work" / ("sql-retirement-guards-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    print(json.dumps(install(args.app_root, backup_root, apply=args.apply), indent=2))


if __name__ == "__main__":
    main()
