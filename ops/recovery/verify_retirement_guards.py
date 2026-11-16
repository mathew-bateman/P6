"""Run through manage.py shell after guard deployment; all ORM writes are mocked."""
import json
from types import SimpleNamespace
from unittest.mock import patch, Mock

from django.test import RequestFactory
from django.template.loader import render_to_string
from reporting_mirror import forms, models, sql_parallel, sync, tasks, views


checks = []
config = models.ReportingConfig(
    pk=1, enabled=True, serve_reports=True, calculation_engine="postgres",
    manage_settings_in_postgres=True, sql_parallel_enabled=False,
    interval_minutes=60, stale_after_minutes=120, query_timeout_seconds=60,
    retained_generations=1,
)

# A forged POST cannot bring back either SQL calculations or parallel refresh.
with patch.object(forms, "retired", return_value=True), patch.object(forms, "reporting_available", return_value=True), patch.object(models.ReportingRun.objects, "filter") as successful:
    successful.return_value.exists.return_value = True
    form = forms.ReportingSettingsForm({
        "enabled": "on", "serve_reports": "on", "calculation_engine": "sql",
        "sql_parallel_enabled": "on", "interval_minutes": "60",
        "stale_after_minutes": "120", "query_timeout_seconds": "60",
        "retained_generations": "1",
    }, instance=config)
    assert form.is_valid(), dict(form.errors)
    assert form.cleaned_data["calculation_engine"] == "postgres"
    assert form.cleaned_data["sql_parallel_enabled"] is False
    assert form.fields["calculation_engine"].disabled
    assert form.fields["sql_parallel_enabled"].disabled
checks.append("forged settings POST retains PostgreSQL and disables parallel SQL")

with patch.object(models.ReportingConfig, "current", return_value=config), patch("reporting_mirror.sql_retirement.retired", return_value=True), patch.object(models.ReportingRun.objects, "create") as create:
    try:
        sync.run_sync(engine="sql", force=True)
    except RuntimeError as error:
        assert "retired" in str(error)
    else:
        raise AssertionError("SQL fallback was not blocked")
    create.assert_not_called()
checks.append("SQL fallback fails before creating or executing a run")

with patch.object(sql_parallel, "retired", return_value=True), patch.object(models.ReportingConfig.objects, "filter") as read_config:
    assert sql_parallel.queue_sql_parallel() is None
    read_config.assert_not_called()
checks.append("parallel SQL cannot be queued")

record = Mock(status="queued")
with patch.object(sql_parallel, "retired", return_value=True), patch.object(models.SQLParallelRun.objects, "get", return_value=record), patch.object(sql_parallel, "postgres_connection") as connection:
    assert sql_parallel.run_sql_parallel("retirement-smoke") == "Parallel SQL reporting is disabled"
    assert record.status == "skipped"
    connection.assert_not_called()
checks.append("already queued parallel SQL is skipped without connecting")

with patch("reporting_mirror.sql_retirement.retired", return_value=True), patch.object(models.NativeEngineValidation.objects, "filter") as validation:
    result = tasks.validate_native_engine.run(validation_id="retirement-smoke")
    assert "retired" in result
    assert validation.return_value.update.call_args.kwargs["status"] == "failed"
checks.append("already queued engine comparison finishes without SQL work")

with patch("reporting_mirror.sql_retirement.retired", return_value=True), patch.object(views.messages, "warning") as warning, patch.object(views.validate_native_engine, "apply_async") as enqueue:
    response = views.NativeEngineValidationView().post(RequestFactory().post("/reporting-database/validate-engine/"))
    assert response.status_code == 302
    warning.assert_called_once()
    enqueue.assert_not_called()
checks.append("engine comparison request explains retirement without queuing")

context = {
    "config": config, "request": SimpleNamespace(user=SimpleNamespace(is_staff=True)),
    "reporting_available": True, "sql_reporting_retired": True,
    "source_rows": [], "sql_parallel_runs": [SimpleNamespace(status="success", progress="Archived successful SQL calculation", started_at=None, sql_config_version_id=37, processed_project_count=820)],
    "csrf_token": "retirement-smoke-token",
}
rendered = render_to_string("reporting_mirror/section_engine.html", context)
assert ">Compare</button>" not in rendered
assert ">Retired</span>" in rendered
assert "SQL views and stored results were retired after a verified recovery backup. PostgreSQL serves current reports." in rendered
assert "Archived SQL refresh history" in rendered
assert "Archived successful SQL calculation" in rendered
assert "SQL refresh paused</span>" not in rendered
context["sql_reporting_retired"] = False
assert ">Compare</button>" in render_to_string("reporting_mirror/section_engine.html", context)
checks.append("retired display hides Compare and preserves archived SQL history")

print(json.dumps({"status": "passed", "checks": checks, "database_mutations": False}, indent=2))
