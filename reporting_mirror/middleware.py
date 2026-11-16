from django.http import HttpResponseBadRequest
from django.template.response import TemplateResponse
import logging
import psycopg

from reporting_mirror.reader import report_source, snapshot_context, report_connection, report_publication, active_config
from reporting_mirror.connections import postgres_connection


class ReportingSourceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        applies = request.path.startswith("/portfolio-reporting/") or request.path.startswith(("/schedule-quality/overview/", "/schedule-quality/validation/"))
        if not applies:
            return self.get_response(request)
        from backups.views import can_view_portfolio_reporting, can_view_schedule_quality_reports
        permitted = can_view_portfolio_reporting(request.user) if request.path.startswith("/portfolio-reporting/") else can_view_schedule_quality_reports(request.user)
        config = active_config() if permitted else None
        default_source = "combined" if config else "p6_22"
        source = request.GET.get("report_source", request.session.get("p6_report_source", default_source))
        if source not in {"p6_22", "p6_23", "combined"}:
            return HttpResponseBadRequest("Choose P6 22, P6 23 or both versions as the reporting source.")
        if request.session.get("p6_report_source") != source:
            request.session["p6_report_source"] = source
        request.p6_report_source = source
        token = report_source.set(source)
        try:
            if config:
                # Every query in one rendered report sees one publication, even
                # if an hourly refresh commits while the page is being built.
                with postgres_connection(reader=True) as connection:
                    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    connection.execute("SELECT set_config('statement_timeout',%s,true)", (str(config.query_timeout_seconds * 1000),))
                    publication = connection.execute("SELECT * FROM reporting.refresh_status").fetchone()
                    connection_token = report_connection.set(connection)
                    publication_token = report_publication.set(publication)
                    try:
                        return self.get_response(request)
                    finally:
                        report_publication.reset(publication_token)
                        report_connection.reset(connection_token)
            return self.get_response(request)
        except psycopg.Error:
            logging.getLogger(__name__).exception("The reporting database is temporarily unavailable.")
            return TemplateResponse(request, "reporting_mirror/unavailable.html", status=503).render()
        finally:
            report_source.reset(token)

    def process_template_response(self, request, response):
        if hasattr(request, "p6_report_source") and hasattr(response, "context_data"):
            response.context_data.update(snapshot_context(request.p6_report_source))
            response.context_data["reporting_is_partial"] = request.headers.get("HX-Request") == "true"
        return response
