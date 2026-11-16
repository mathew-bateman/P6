from datetime import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView
from psycopg import sql

from reporting_mirror.access import can_refresh_reporting
from reporting_mirror.connections import postgres_connection, reporting_available
from reporting_mirror.sql_retirement import retired
from reporting_mirror.forms import ReportingSettingsForm
from reporting_mirror.models import ReportingConfig, ReportingRun, ReportingSource, NativeEngineValidation, NativeSettingsCheckpoint, SQLParallelRun
from reporting_mirror.tasks import refresh_reporting, sync_schedule, validate_native_engine, switch_reporting_engine


class ReportingAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        from backups.views import can_view_portfolio_reporting, can_view_schedule_quality_reports
        user = self.request.user
        return can_refresh_reporting(user) or can_view_portfolio_reporting(user) or can_view_schedule_quality_reports(user)


class ReportingRefreshMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return can_refresh_reporting(self.request.user)


class ReportingStaffMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff


def dashboard_context():
    config = ReportingConfig.current()
    latest = ReportingRun.objects.filter(status="success").first()
    source_rows = []
    for source in ReportingSource.objects.select_related("target"):
        data = (latest.sources.get(source.code, {}) if latest else {}).copy()
        captured = datetime.fromisoformat(data["snapshot_at"]) if data.get("snapshot_at") else None
        source_rows.append({"code": source.code, "label": source.get_code_display(), "database": source.target.sql_database, "snapshot_at": captured, "counts": data.get("counts", {}), "quality": data.get("quality", {}), "stale": captured is None or (timezone.now() - captured).total_seconds() > config.stale_after_minutes * 60})
    views = {}
    for entry in latest.manifest if latest else []:
        if entry["kind"] != "view":
            continue
        row = views.setdefault(entry["name"], {"name": entry["name"], "p6_22": 0, "p6_23": 0})
        row[entry["source"]] = entry["rows"]
    available = reporting_available()
    latest_attempt = ReportingRun.objects.exclude(status="skipped").first()
    # Publication and latest attempt are distinct: a failed attempt leaves the
    # previous publication in place and must never be shown as a green success.
    if not available:
        publication_health = "unavailable"
    elif latest_attempt and latest_attempt.status == "failed":
        publication_health = "failed"
    elif (not latest or not latest.published_at) and latest_attempt and latest_attempt.status == "running":
        publication_health = "running"
    elif not latest or not latest.published_at:
        publication_health = "pending"
    elif {row["code"] for row in source_rows} != {"p6_22", "p6_23"} or any(row["stale"] for row in source_rows):
        publication_health = "stale"
    elif latest_attempt and latest_attempt.status == "running":
        publication_health = "running"
    else:
        publication_health = "healthy"
    return {
        "config": config, "reporting_available": available,
        "sql_reporting_retired": retired(),
        "publication_health": publication_health, "latest_attempt": latest_attempt,
        "latest_publication": latest, "source_rows": source_rows,
        "catalogue": list(views.values()), "recent_runs": ReportingRun.objects.all()[:10],
        "settings_form": ReportingSettingsForm(instance=config),
        "engine_validation": NativeEngineValidation.objects.first(),
        "settings_checkpoint": NativeSettingsCheckpoint.objects.filter(pk=1).first(),
        "native_validation_running": NativeEngineValidation.objects.filter(status__in=["queued", "running"]).exists(),
        "sql_parallel_runs": SQLParallelRun.objects.all()[:5],
        "sql_parallel_latest": SQLParallelRun.objects.first(),
        "total_projects": sum(s["counts"].get("projects", 0) for s in source_rows),
        "total_activities": sum(s["counts"].get("activities", 0) for s in source_rows),
        "total_relationships": sum(s["counts"].get("relationships", 0) for s in source_rows),
    }


class ReportingDashboardView(ReportingAccessMixin, TemplateView):
    template_name = "reporting_mirror/dashboard.html"

    def get_context_data(self, **kwargs):
        context = {**super().get_context_data(**kwargs), **dashboard_context(), "can_refresh_reporting": can_refresh_reporting(self.request.user)}
        if self.request.GET.get("section") == "history":
            from urllib.parse import urlencode
            from django.urls import reverse
            status = self.request.GET.get("status", "")
            allowed = dict(ReportingRun._meta.get_field("status").choices)
            if status not in allowed:
                status = ""
            runs = ReportingRun.objects.all()
            if status:
                runs = runs.filter(status=status)
            page = Paginator(runs, 10).get_page(self.request.GET.get("page"))
            def history_url(**values):
                query = {"section": "history"}
                source = self.request.GET.get("report_source", "")
                if source in {"combined", "p6_22", "p6_23"}:
                    query["report_source"] = source
                query.update(values)
                return reverse("reporting_mirror_dashboard") + "?" + urlencode(query)
            context.update({
                "recent_runs": page.object_list,
                "history_page": page,
                "history_pages": [{"label": number, "active": number == page.number, "url": history_url(status=status, page=number) if isinstance(number, int) else ""} for number in page.paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1)],
                "history_filters": [{"label": label, "active": value == status, "url": history_url(status=value)} for value, label in [("", "All runs"), *allowed.items()]],
                "history_previous": history_url(status=status, page=page.previous_page_number()) if page.has_previous() else "",
                "history_next": history_url(status=status, page=page.next_page_number()) if page.has_next() else "",
            })
        return context

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true" and self.request.headers.get("HX-History-Restore-Request") != "true" and self.request.GET.get("section") == "history":
            return ["reporting_mirror/section_history.html"]
        return super().get_template_names()


class ReportingSettingsView(ReportingStaffMixin, View):
    def post(self, request):
        config = ReportingConfig.current()
        previous_engine = config.calculation_engine
        previously_parallel = config.sql_parallel_enabled
        form = ReportingSettingsForm(request.POST, instance=config)
        if form.is_valid():
            instance = form.save(commit=False)
            requested_engine = form.cleaned_data["calculation_engine"]
            instance.calculation_engine = previous_engine
            if requested_engine != previous_engine:
                instance.pending_engine = requested_engine
            instance.changed_by = request.user.get_username()
            instance.save()
            if instance.sql_parallel_enabled and not previously_parallel:
                from reporting_mirror.sql_parallel import queue_sql_parallel
                queue_sql_parallel(trigger="manual")
            schedule = sync_schedule(instance)
            if requested_engine != previous_engine:
                # The immediate switch performs a fresh capture. Anchor the
                # hourly timer here so it does not queue a duplicate mid-switch.
                schedule.last_run_at = timezone.now()
                schedule.save(update_fields=['last_run_at'])
            if not instance.serve_reports:
                request.session["p6_report_source"] = "p6_22"
            if requested_engine != previous_engine:
                try:
                    switch_reporting_engine.apply_async(kwargs={"engine": requested_engine, "requested_by": request.user.get_username()}, queue="p6_reporting")
                    messages.success(request, "Engine switch queued. Reports keep using the last successful results until both sources are ready.")
                except Exception:
                    ReportingConfig.objects.filter(pk=1).update(pending_engine="")
                    messages.error(request, "The engine switch could not be queued. The current calculation engine is unchanged.")
            messages.success(request, "Reporting settings saved.")
            return redirect("reporting_mirror_dashboard")
        from django.template.response import TemplateResponse
        return TemplateResponse(request, "reporting_mirror/dashboard.html", {**dashboard_context(), "settings_form": form, "can_refresh_reporting": can_refresh_reporting(request.user)}, status=400)


class ReportingRefreshView(ReportingRefreshMixin, View):
    def post(self, request):
        if not reporting_available():
            messages.error(request, "This recovery host uses its original SQL reports. PostgreSQL refresh is configured on XPS-Server-4.")
            return redirect("reporting_mirror_dashboard")
        refresh_reporting.apply_async(kwargs={"trigger": "manual", "requested_by": request.user.get_username(), "force": True}, queue="p6_reporting")
        messages.success(request, "A reporting refresh has been queued. Both sources will be validated before publication.")
        return redirect("reporting_mirror_dashboard")


class ReportingPreviewView(ReportingAccessMixin, TemplateView):
    template_name = "reporting_mirror/preview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        latest = ReportingRun.objects.filter(status="success").first()
        name = self.kwargs["name"]
        schema = self.request.GET.get("source", "reporting")
        if not latest or not reporting_available() or schema not in {"reporting", "p6_22", "p6_23"}:
            raise Http404
        manifest = [m for m in latest.manifest if m["name"] == name and m["kind"] == "view" and (schema == "reporting" or m["source"] == schema)]
        if not manifest:
            raise Http404
        try:
            page = max(1, int(self.request.GET.get("page", 1)))
        except (ValueError, TypeError):
            page = 1
        count = sum(m["rows"] for m in manifest)
        total_pages = max(1, (count + 49) // 50)
        page = min(page, total_pages)
        columns = [c["name"] for c in manifest[0]["columns"]]
        order = [c for c in ("_source_system", "proj_id", "task_id", "check_code", "sort_order") if c in columns or c == "_source_system"]
        with postgres_connection(reader=True) as connection:
            rows = connection.execute(sql.SQL("SELECT * FROM {}.{} ORDER BY {} LIMIT 50 OFFSET %s").format(sql.Identifier(schema), sql.Identifier(name), sql.SQL(",").join(map(sql.Identifier, order))), ((page - 1) * 50,)).fetchall()
        display_columns = list(rows[0]) if rows else [*columns, "_source_system", "_snapshot_at", "_synced_at"]
        metadata = [c for c in display_columns if c.startswith("_")]
        display_columns = metadata + [c for c in display_columns if c not in metadata]
        context.update({"view_name": name, "schema": schema, "columns": display_columns, "rows": [[row.get(c) for c in display_columns] for row in rows], "row_count": count, "page_number": page, "total_pages": total_pages, "previous_page": page - 1 if page > 1 else None, "next_page": page + 1 if page < total_pages else None, "published_at": latest.published_at})
        return context


class NativeEngineValidationView(ReportingStaffMixin, View):
    def post(self, request):
        from reporting_mirror.sql_retirement import retired, MESSAGE
        if retired():
            messages.warning(request, MESSAGE)
            return redirect("reporting_mirror_dashboard")
        if not reporting_available():
            messages.error(request, "Engine validation is configured on XPS-Server-4.")
            return redirect("reporting_mirror_dashboard")
        if NativeEngineValidation.objects.filter(status__in=["queued", "running"]).exists():
            messages.info(request, "An engine validation is already queued or running.")
            return redirect("reporting_mirror_dashboard")
        record = NativeEngineValidation.objects.create(status="queued", requested_by=request.user.get_username(), progress="Queued for isolated comparison of both engines")
        try:
            validate_native_engine.apply_async(kwargs={"validation_id": str(record.pk)}, queue="p6_reporting")
        except Exception:
            record.status="failed";record.progress="Validation could not be queued";record.finished_at=timezone.now();record.save()
            messages.error(request, "Validation could not be queued; reports and calculation engine are unchanged.")
        else:
            messages.success(request, "Validation queued. Both engines will use the same isolated P6 snapshots; current reports stay available.")
        return redirect("reporting_mirror_dashboard")
