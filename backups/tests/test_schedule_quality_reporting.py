from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from backups.services.schedule_quality_reporting import (
    ScheduleQualityValidationFilters,
    fetch_programme_overview,
    fetch_validation_evidence,
    fetch_validation_filter_options,
    fetch_validation_summary,
)


class ScheduleQualityReportingServiceTests(SimpleTestCase):
    @patch("backups.services.schedule_quality_reporting.fetch_rows")
    def test_filter_options_are_derived_from_project_dimensions(self, fetch_rows):
        fetch_rows.side_effect = [
            [
                {
                    "proj_id": 7,
                    "proj_short_name": "Project Seven",
                    "portfolio": "Rail",
                    "lead_planner": "Alex",
                    "project_status": "Client Submitted",
                    "project_state": "Live",
                    "updated_date": date(2026, 8, 14),
                },
                {
                    "proj_id": 8,
                    "proj_short_name": "Project Eight",
                    "portfolio": "Rail",
                    "lead_planner": "Beth",
                    "project_status": "In Progress",
                    "project_state": "Draft",
                    "updated_date": date(2026, 8, 13),
                },
            ],
            [{"check_code": "high_float", "display_name": "High Total Float", "sort_order": 90}],
        ]

        result = fetch_validation_filter_options()

        self.assertEqual(result["portfolios"], ["Rail"])
        self.assertEqual(result["lead_planners"], ["Alex", "Beth"])
        self.assertEqual(result["project_statuses"], ["Client Submitted", "In Progress"])
        self.assertEqual(result["project_states"], ["Draft", "Live"])
        self.assertEqual(
            result["updated_dates"],
            [date(2026, 8, 14), date(2026, 8, 13)],
        )

    @patch("backups.services.schedule_quality_reporting.fetch_rows")
    def test_summary_uses_the_correct_activity_and_relationship_denominators(self, fetch_rows):
        fetch_rows.side_effect = [
            [
                {
                    "dcma_activity_count": 258,
                    "relationship_count": 362,
                    "missing_predecessor_count": 1,
                    "non_fs_count": 4,
                }
            ],
            [
                {
                    "check_code": "missing_predecessor",
                    "display_name": "Missing Predecessors",
                    "sort_order": 10,
                },
                {
                    "check_code": "relationship_ratio",
                    "display_name": "Relationship Ratio / Non-FS",
                    "sort_order": 70,
                },
            ],
        ]

        rows = fetch_validation_summary(ScheduleQualityValidationFilters())

        self.assertEqual(rows[0]["records_checked"], 258)
        self.assertEqual(rows[0]["qualifying_percent"], Decimal("0.39"))
        self.assertEqual(rows[1]["records_checked"], 362)
        self.assertEqual(rows[1]["qualifying_results"], 1)
        self.assertEqual(rows[1]["qualifying_percent"], Decimal("0.39"))
        self.assertEqual(rows[1]["qualifying_display"], "0.39%")

    @patch("backups.services.schedule_quality_reporting.fetch_rows")
    def test_evidence_query_applies_filters_and_pagination_as_parameters(self, fetch_rows):
        fetch_rows.side_effect = [
            [{"row_count": 1}],
            [{"proj_id": 7, "task_code": "A1000"}],
        ]
        filters = ScheduleQualityValidationFilters(
            project_id=7,
            portfolio="Rail",
            lead_planner="Alex",
            project_status="Client Submitted",
            project_state="Live",
            exclude_blanks=True,
            updated_date=date(2026, 8, 14),
            check_code="high_float",
        )

        rows, total = fetch_validation_evidence(filters, limit=25, offset=50)

        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["task_code"], "A1000")
        count_parameters = fetch_rows.call_args_list[0].kwargs["parameters"]
        page_parameters = fetch_rows.call_args_list[1].kwargs["parameters"]
        self.assertEqual(
            count_parameters,
            (7, "Rail", "Alex", "Client Submitted", "Live", date(2026, 8, 14), "high_float"),
        )
        self.assertIn("NULLIF(LTRIM(RTRIM(project.project_status)), '') IS NOT NULL", fetch_rows.call_args_list[0].args[1])
        self.assertEqual(page_parameters[-2:], (50, 25))

    @patch("backups.services.schedule_quality_reporting.fetch_rows")
    def test_programme_overview_applies_pbix_limits_and_points(self, fetch_rows):
        fetch_rows.side_effect = [
            [
                {
                    "dcma_activity_count": 258,
                    "relationship_count": 362,
                    "logical_loop_count": 0,
                    "high_float_count": 34,
                    "negative_float_count": 0,
                    "latest_updated_date": date(2026, 6, 4),
                }
            ],
            [
                {"check_code": "logical_loops", "display_name": "Logical Loops"},
                {"check_code": "high_float", "display_name": "High Total Float"},
                {"check_code": "negative_float", "display_name": "Negative Float"},
            ],
        ]

        overview = fetch_programme_overview(ScheduleQualityValidationFilters())

        self.assertEqual([row["result"] for row in overview["rows"]], ["Green", "Red", "Green"])
        self.assertEqual(overview["total_points_available"], 80)
        self.assertEqual(overview["total_points_achieved"], 70)
        self.assertEqual(overview["pass_percent"], Decimal("87.50"))
        self.assertEqual(overview["pass_or_fail"], "PASS")

    @patch("backups.services.schedule_quality_reporting.fetch_rows")
    def test_programme_overview_calculates_relationship_ratio(self, fetch_rows):
        fetch_rows.side_effect = [
            [
                {
                    "dcma_activity_count": 258,
                    "relationship_count": 362,
                    "non_fs_count": 4,
                    "latest_updated_date": date(2026, 6, 4),
                }
            ],
            [{"check_code": "relationship_ratio", "display_name": "Relationship Ratio"}],
        ]

        overview = fetch_programme_overview(ScheduleQualityValidationFilters())

        row = overview["rows"][0]
        self.assertEqual(row["records_checked"], 362)
        self.assertEqual(row["qualifying_results"], 1)
        self.assertEqual(row["qualifying_display"], "1.40")
        self.assertEqual(row["result"], "Green")

    @patch("backups.services.schedule_quality_reporting.fetch_rows")
    def test_validation_summary_formats_relationship_ratio_like_pbix(self, fetch_rows):
        fetch_rows.side_effect = [
            [
                {
                    "dcma_activity_count": 258,
                    "relationship_count": 362,
                    "non_fs_count": 4,
                }
            ],
            [{"check_code": "relationship_ratio", "display_name": "Relationship Ratio", "sort_order": 1}],
        ]

        summary = fetch_validation_summary(ScheduleQualityValidationFilters())

        self.assertEqual(summary[0]["qualifying_results"], 1)
        self.assertEqual(summary[0]["qualifying_display"], "0.39%")
        self.assertEqual(summary[0]["qualifying_percent"], Decimal("0.39"))


class ScheduleQualityValidationViewTests(TestCase):
    def setUp(self):
        self.staff_user = get_user_model().objects.create_user(
            username="staff-reporter",
            password="password",
            is_staff=True,
        )
        self.viewer = get_user_model().objects.create_user(
            username="viewer",
            password="password",
        )

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("schedule_quality_validation"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_non_staff_user_is_denied(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("schedule_quality_validation"))

        self.assertEqual(response.status_code, 403)

    @patch("backups.views.fetch_schedule_quality_refresh_history")
    @patch("backups.views.fetch_validation_evidence")
    @patch("backups.views.fetch_validation_summary")
    @patch("backups.views.fetch_validation_filter_options")
    def test_staff_user_can_view_validation_report(
        self,
        filter_options,
        summary,
        evidence,
        refresh_history,
    ):
        filter_options.return_value = {
            "projects": [{"proj_id": 7, "proj_short_name": "Project Seven"}],
            "portfolios": ["Rail"],
            "lead_planners": ["Alex"],
            "updated_dates": [date(2026, 8, 14)],
            "checks": [{"check_code": "high_float", "display_name": "High Total Float"}],
        }
        summary.return_value = [
            {
                "number": 1,
                "check_code": "high_float",
                "check_name": "High Total Float",
                "records_checked": 143287,
                "qualifying_results": 2779,
                "qualifying_percent": Decimal("1.94"),
                "status": "review",
            }
        ]
        evidence.return_value = (
            [
                {
                    "lead_planner": "Alex",
                    "proj_short_name": "Project Seven",
                    "proj_id": 7,
                    "check_name": "High Total Float",
                    "task_code": "A1000",
                    "task_name": "Build platform",
                    "evidence_display": "Total Float: 90.00 days",
                }
            ],
            1,
        )
        refresh_history.return_value = [
            {
                "check_run_id": 101,
                "completed_at": None,
                "duration_display": "54s",
            }
        ]
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("schedule_quality_validation"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Schedule Quality Validation")
        self.assertContains(response, "High Total Float")
        self.assertContains(response, "1.94%")
        self.assertContains(response, "143,287")
        self.assertContains(response, "2,779")
        self.assertContains(response, "Build platform")
        self.assertContains(response, "Total Float: 90.00 days")
        self.assertContains(response, 'hx-trigger="change from:select, change from:input"')
        self.assertContains(response, "syncScheduleQualityReportTabs")
        self.assertNotContains(response, ">Apply<")

    @patch("backups.views.fetch_schedule_quality_refresh_history", return_value=[])
    @patch("backups.views.fetch_validation_evidence", return_value=([], 0))
    @patch("backups.views.fetch_validation_summary", return_value=[])
    @patch(
        "backups.views.fetch_validation_filter_options",
        return_value={
            "projects": [],
            "portfolios": [],
            "lead_planners": [],
            "updated_dates": [date(2026, 8, 1)],
            "checks": [],
        },
    )
    def test_htmx_filter_request_returns_results_fragment(
        self,
        _filter_options,
        _summary,
        _evidence,
        _refresh_history,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.get(
            reverse("schedule_quality_validation"),
            {"project": "7"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="validation-results"')
        self.assertNotContains(response, "Schedule Quality Validation")
        self.assertNotContains(response, "<html")

    @patch("backups.views.fetch_schedule_quality_refresh_history", return_value=[])
    @patch("backups.views.fetch_validation_evidence", return_value=([], 0))
    @patch("backups.views.fetch_validation_summary", return_value=[])
    @patch(
        "backups.views.fetch_validation_filter_options",
        return_value={
            "projects": [],
            "portfolios": [],
            "lead_planners": [],
            "updated_dates": [],
            "checks": [],
        },
    )
    def test_staff_filters_are_forwarded_to_reporting_service(
        self,
        _filter_options,
        summary,
        evidence,
        _refresh_history,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.get(
            reverse("schedule_quality_validation"),
            {
                "project": "7",
                "portfolio": "Rail",
                "lead_planner": "Alex",
                "project_status": "Client Submitted",
                "project_state": "Live",
                "exclude_blanks": "1",
                "updated_date": "2026-08-14",
                "check": "high_float",
            },
        )

        self.assertEqual(response.status_code, 200)
        filters = summary.call_args.args[0]
        self.assertEqual(filters.project_id, 7)
        self.assertEqual(filters.updated_date, date(2026, 8, 14))
        self.assertEqual(filters.check_code, "high_float")
        self.assertEqual(filters.project_status, "Client Submitted")
        self.assertEqual(filters.project_state, "Live")
        self.assertTrue(filters.exclude_blanks)
        self.assertEqual(evidence.call_args.args[0], filters)
        self.assertContains(
            response,
            'href="/schedule-quality/overview/?project=7&amp;portfolio=Rail&amp;lead_planner=Alex&amp;project_status=Client+Submitted&amp;project_state=Live&amp;exclude_blanks=1&amp;updated_date=2026-08-14&amp;check=high_float"',
        )


class ScheduleQualityOverviewViewTests(TestCase):
    def setUp(self):
        self.staff_user = get_user_model().objects.create_user(
            username="staff-overview",
            password="password",
            is_staff=True,
        )
        self.viewer = get_user_model().objects.create_user(
            username="overview-viewer",
            password="password",
        )

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("schedule_quality_overview"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_non_staff_user_is_denied(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("schedule_quality_overview"))

        self.assertEqual(response.status_code, 403)

    @patch("backups.views.fetch_schedule_quality_refresh_history", return_value=[])
    @patch("backups.views.fetch_programme_overview")
    @patch("backups.views.fetch_validation_filter_options")
    def test_staff_user_can_view_programme_overview(
        self,
        filter_options,
        overview,
        _refresh_history,
    ):
        filter_options.return_value = {
            "projects": [{"proj_id": 7, "proj_short_name": "Project Seven"}],
            "portfolios": ["Rail"],
            "lead_planners": ["Alex"],
            "updated_dates": [date(2026, 6, 4)],
            "checks": [],
        }
        overview.return_value = {
            "rows": [
                {
                    "description": "Logical Loops",
                    "result": "Green",
                    "records_checked": 258,
                    "qualifying_results": 0,
                    "qualifying_display": "0",
                    "green_limit": Decimal("0"),
                    "amber_limit": Decimal("1"),
                    "limit_type": "Number",
                    "green_points": 50,
                    "amber_points": 40,
                    "points_scored": 0,
                }
            ],
            "latest_updated_date": date(2026, 6, 4),
            "total_points_available": 335,
            "total_points_achieved": 285,
            "pass_percent": Decimal("85.07"),
            "pass_rate": Decimal("85.00"),
            "pass_or_fail": "PASS",
        }
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("schedule_quality_overview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Programme Check")
        self.assertContains(response, "Logical Loops")
        self.assertContains(response, 'hx-swap="outerHTML"')
        self.assertContains(response, "htmx:load")
        self.assertContains(response, "syncScheduleQualityReportTabs")
        self.assertContains(response, "85.07%")
        self.assertContains(response, "Validation &amp; Evidence")
        self.assertContains(response, "Latest update")
        self.assertContains(response, "Scorecard")
        self.assertContains(response, 'class="numeric-cell points-scored-zero"')
        self.assertContains(response, ">Overview<")
        self.assertNotContains(response, "Green and amber limits mirror the PBIX scoring model.")
        self.assertNotContains(response, "Score the programme against the active schedule-quality checks.")
        self.assertNotContains(response, "Choose the programme slice to score.")
        self.assertContains(response, 'hx-trigger="change from:select, change from:input"')
        self.assertNotContains(response, ">Apply<")

    @patch("backups.views.fetch_schedule_quality_refresh_history", return_value=[])
    @patch(
        "backups.views.fetch_programme_overview",
        return_value={
            "rows": [],
            "latest_updated_date": None,
            "total_points_available": 0,
            "total_points_achieved": 0,
            "pass_percent": Decimal("0"),
            "pass_rate": Decimal("85"),
            "pass_or_fail": "FAIL",
        },
    )
    @patch(
        "backups.views.fetch_validation_filter_options",
        return_value={
            "projects": [],
            "portfolios": [],
            "lead_planners": [],
            "updated_dates": [date(2026, 8, 1)],
            "checks": [],
        },
    )
    def test_htmx_filter_request_returns_overview_fragment(
        self,
        _filter_options,
        _overview,
        _refresh_history,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.get(
            reverse("schedule_quality_overview"),
            {"project": "7"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="panel overview-filters"')
        self.assertNotContains(response, "Programme Check")
        self.assertNotContains(response, "<html")

    @patch("backups.views.fetch_schedule_quality_refresh_history", return_value=[])
    @patch(
        "backups.views.fetch_programme_overview",
        return_value={
            "rows": [],
            "latest_updated_date": None,
            "total_points_available": 0,
            "total_points_achieved": 0,
            "pass_percent": Decimal("0"),
            "pass_rate": Decimal("85"),
            "pass_or_fail": "FAIL",
        },
    )
    @patch(
        "backups.views.fetch_validation_filter_options",
        return_value={
            "projects": [
                {"proj_id": 7, "proj_short_name": "Rail Project", "portfolio": "Rail"},
                {"proj_id": 8, "proj_short_name": "Road Project", "portfolio": "Road"},
            ],
            "portfolios": ["Rail", "Road"],
            "lead_planners": [],
            "updated_dates": [date(2026, 8, 1)],
            "checks": [],
        },
    )
    def test_project_selection_resolves_and_scopes_its_portfolio(
        self,
        _filter_options,
        overview,
        _refresh_history,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("schedule_quality_overview"), {"project": "7"})

        self.assertEqual(response.status_code, 200)
        filters = overview.call_args.args[0]
        self.assertEqual(filters.project_id, 7)
        self.assertEqual(filters.portfolio, "Rail")
        self.assertContains(response, "All projects in Rail")
        self.assertContains(response, "Rail Project")
        self.assertNotContains(response, "Road Project")
        self.assertContains(response, 'href="/schedule-quality/validation/?project=7"')

    @patch("backups.views.fetch_schedule_quality_refresh_history", return_value=[])
    @patch(
        "backups.views.fetch_programme_overview",
        return_value={
            "rows": [], "latest_updated_date": None, "total_points_available": 0,
            "total_points_achieved": 0, "pass_percent": Decimal("0"),
            "pass_rate": Decimal("85"), "pass_or_fail": "FAIL",
        },
    )
    @patch(
        "backups.views.fetch_validation_filter_options",
        return_value={
            "projects": [
                {"proj_id": 7, "proj_short_name": "Rail Old", "portfolio": "Rail", "updated_date": date(2026, 7, 1)},
                {"proj_id": 9, "proj_short_name": "Rail Latest", "portfolio": "Rail", "updated_date": date(2026, 8, 1)},
                {"proj_id": 8, "proj_short_name": "Road Latest", "portfolio": "Road", "updated_date": date(2026, 8, 2)},
            ],
            "portfolios": ["Rail", "Road"],
            "lead_planners": [],
            "updated_dates": [date(2026, 8, 1)],
            "checks": [],
        },
    )
    def test_portfolio_selection_defaults_to_its_latest_submission(
        self,
        _filter_options,
        overview,
        _refresh_history,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("schedule_quality_overview"), {"portfolio": "Rail"})

        self.assertEqual(response.status_code, 200)
        filters = overview.call_args.args[0]
        self.assertEqual(filters.portfolio, "Rail")
        self.assertEqual(filters.project_id, 9)
        self.assertContains(response, 'value="9" selected')
        self.assertContains(response, "✓ 01/08/2026 — Rail Latest")
        self.assertContains(response, 'id="overview-date"')
        self.assertContains(response, 'class="schedule-quality-date-picker"')
        self.assertContains(response, 'data-updated-dates="2026-08-01"')
        self.assertContains(response, 'instance.jumpToDate(availableDates[0])')
        self.assertLess(
            response.content.find(b"Rail Latest"),
            response.content.find(b"Rail Old"),
        )

    @patch("backups.views.fetch_schedule_quality_refresh_history", return_value=[])
    @patch(
        "backups.views.fetch_programme_overview",
        return_value={
            "rows": [], "latest_updated_date": None, "total_points_available": 0,
            "total_points_achieved": 0, "pass_percent": Decimal("0"),
            "pass_rate": Decimal("85"), "pass_or_fail": "FAIL",
        },
    )
    @patch(
        "backups.views.fetch_validation_filter_options",
        return_value={
            "projects": [{"proj_id": 7, "proj_short_name": "Project Seven", "portfolio": None}],
            "portfolios": [], "lead_planners": [], "updated_dates": [], "checks": [],
        },
    )
    def test_project_selection_is_available_without_a_portfolio(
        self,
        _filter_options,
        _overview,
        _refresh_history,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("schedule_quality_overview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "All projects")
        self.assertContains(response, "Project Seven")
        self.assertNotContains(response, 'name="project" disabled')
