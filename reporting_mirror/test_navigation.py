from contextlib import ExitStack
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs

from django.http import HttpResponse
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from reporting_mirror.middleware import ReportingSourceMiddleware
from reporting_mirror.reader import report_source
from reporting_mirror.templatetags.reporting_navigation import report_query


class ReportingNavigationTests(SimpleTestCase):
    def request(self, query=None, source="combined"):
        request = RequestFactory().get("/schedule-quality/validation/", query or {})
        request.session = {"p6_report_source": source} if source else {}
        request.user = SimpleNamespace(is_authenticated=False)
        if source:
            request.p6_report_source = source
        return request

    def test_links_keep_session_source_and_encoded_filters_without_query_source(self):
        request = self.request({"portfolio": "Rail & Air", "project": "8589934599", "page": "3"}, "p6_23")
        query = report_query({"request": request}, page=2)
        self.assertEqual(parse_qs(query[1:]), {"portfolio": ["Rail & Air"], "project": ["8589934599"], "page": ["2"], "report_source": ["p6_23"]})

    def test_clear_filters_keeps_source_only(self):
        request = self.request({"portfolio": "Rail", "project": "77", "page": "3"})
        self.assertEqual(report_query({"request": request}, reset=True), "?report_source=combined")

    def test_entry_links_do_not_force_a_source_before_middleware_selects_default(self):
        self.assertEqual(report_query({"request": self.request(source=None)}, reset=True), "")

    def test_source_switch_preserves_cross_source_filters_but_clears_project_and_pages(self):
        request = self.request({"portfolio": "Rail & Air", "project": "8589934599", "page": "3", "evidence_page": "4", "check": "negative_float", "updated_date": "2026-10-25", "project_status": "__all__", "unexpected": "ignored"})
        html = render_to_string("reporting_mirror/source_status.html", {"request": request, "reporting_snapshot": {"source": "combined"}})
        self.assertIn('name="portfolio" value="Rail &amp; Air"', html)
        self.assertIn('name="check" value="negative_float"', html)
        self.assertIn('name="updated_date" value="2026-10-25"', html)
        self.assertIn('name="project_status" value="__all__"', html)
        for name in ("project", "page", "evidence_page", "unexpected"):
            self.assertNotIn(f'name="{name}"', html)

    def test_hub_links_explicitly_carry_selected_source(self):
        html = render_to_string("backups/portfolio_reporting_hub.html", {"request": self.request(source="p6_23"), "portfolio_report_pages": [{"url_name": "portfolio_reporting_overview", "title": "Overview"}]})
        self.assertIn('href="/portfolio-reporting/overview/?report_source=p6_23"', html)

    def test_hidden_filter_source_is_present_for_original_sql_fallback(self):
        html = Template("{% load reporting_navigation %}{% report_source_input %}").render(Context({"request": self.request(source="p6_22")}))
        self.assertEqual(html, '<input type="hidden" name="report_source" value="p6_22">')

    def test_validation_pagination_retains_selected_source_without_explicit_query(self):
        request = self.request({"check": "negative_float"}, "p6_23")
        html = render_to_string("backups/partials/schedule_quality_validation_results.html", {"request": request, "evidence_page": {"total_pages": 2, "has_next": True, "next_page_number": 2, "page_range": [1, 2], "number": 1}})
        self.assertIn('href="?check=negative_float&amp;report_source=p6_23&amp;page=2"', html)

    def test_combined_project_selector_keeps_existing_source_labels_and_distinct_ids(self):
        request = self.request()
        html = render_to_string("backups/partials/schedule_quality_overview_results.html", {"request": request, "projects": [{"proj_id": 4294967303, "proj_short_name": "Shared name [P6 22]", "updated_date": date(2026, 10, 25)}, {"proj_id": 8589934599, "proj_short_name": "Shared name [P6 23]", "updated_date": date(2026, 10, 25)}]})
        self.assertIn('value="4294967303"', html)
        self.assertIn('value="8589934599"', html)
        self.assertIn("Shared name [P6 22]", html)
        self.assertIn("Shared name [P6 23]", html)


class ReportingSourceDefaultTests(SimpleTestCase):
    def selected(self, *, enabled=True, query=None, session=None):
        request = RequestFactory().get("/portfolio-reporting/", query or {})
        request.session = dict(session or {})
        request.user = object()
        seen = []

        def response(request):
            seen.append(report_source.get())
            return HttpResponse("ok")

        with ExitStack() as stack:
            stack.enter_context(patch("backups.views.can_view_portfolio_reporting", return_value=True))
            stack.enter_context(patch("reporting_mirror.middleware.active_config", return_value=SimpleNamespace(query_timeout_seconds=60) if enabled else None))
            connection = stack.enter_context(patch("reporting_mirror.middleware.postgres_connection"))
            pg = MagicMock()
            connection.return_value.__enter__.return_value = pg
            pg.execute.return_value.fetchone.return_value = {"sources": {}}
            result = ReportingSourceMiddleware(response)(request)
        self.assertEqual(result.status_code, 200)
        return seen[0], request.session

    def test_new_postgres_session_defaults_to_both_versions(self):
        source, session = self.selected()
        self.assertEqual(source, "combined")
        self.assertEqual(session["p6_report_source"], "combined")

    def test_new_sql_fallback_session_defaults_to_p6_22(self):
        source, _ = self.selected(enabled=False)
        self.assertEqual(source, "p6_22")

    def test_explicit_source_overrides_session(self):
        source, session = self.selected(query={"report_source": "p6_23"}, session={"p6_report_source": "p6_22"})
        self.assertEqual(source, "p6_23")
        self.assertEqual(session["p6_report_source"], "p6_23")

    def test_existing_source_selection_is_preserved(self):
        for selected in ("p6_22", "p6_23", "combined"):
            with self.subTest(source=selected):
                source, _ = self.selected(session={"p6_report_source": selected})
                self.assertEqual(source, selected)
