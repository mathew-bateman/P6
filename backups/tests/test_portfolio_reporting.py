from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from backups.services.portfolio_reporting import (
    _project_metric_rows,
    _score_project,
    _where,
    fetch_portfolio_overview,
    fetch_resource_overview,
)
from backups.services.schedule_quality_reporting import ScheduleQualityValidationFilters


class PortfolioReportingServiceTests(SimpleTestCase):
    def test_health_score_uses_supplied_active_database_rules(self):
        row = {
            "dcma_activity_count": 100,
            "high_float_count": 10,
            "negative_float_count": 50,
        }
        rules = [
            {
                "check_code": "high_float",
                "limit_type": "Percent",
                "green_limit": Decimal("15"),
                "amber_limit": Decimal("20"),
                "green_points": 10,
                "amber_points": 8,
            }
        ]

        result = _score_project(row, rules)

        self.assertEqual(result["health_score"], Decimal("100.00"))
        self.assertEqual(result["points_available"], 10)
        self.assertEqual(result["active_check_count"], 1)

    @patch("backups.services.portfolio_reporting.build_schedule_quality_target")
    @patch("backups.services.portfolio_reporting.fetch_rows")
    def test_project_health_uses_normalised_database_denominator(
        self,
        fetch_rows,
        build_target,
    ):
        build_target.return_value.sql_database = "P6"
        fetch_rows.side_effect = [
            [
                {
                    "proj_id": 7,
                    "proj_short_name": "Project Seven",
                    "dcma_activity_count": 100,
                    "open_start_count": 10,
                }
            ],
            [
                {
                    "proj_id": 7,
                    "check_code": "open_start",
                    "records_checked": 20,
                    "qualifying_results": 10,
                    "limit_type": "Percent",
                    "green_limit": Decimal("3"),
                    "amber_limit": Decimal("7"),
                    "green_points": 10,
                    "amber_points": 8,
                }
            ],
        ]

        rows = _project_metric_rows(ScheduleQualityValidationFilters(project_id=7))

        self.assertEqual(rows[0]["health_score"], Decimal("0.00"))
        self.assertIn("xertoolkit_vw_PBI_ScheduleQualityResults", fetch_rows.call_args_list[1].args[1])

    def test_report_where_clause_honours_exclude_blanks(self):
        where_sql, parameters = _where(
            ScheduleQualityValidationFilters(exclude_blanks=True)
        )

        self.assertEqual(parameters, ())
        for field in ("portfolio", "lead_planner", "project_status", "project_state"):
            self.assertIn(f"project.{field}", where_sql)

    @patch("backups.services.portfolio_reporting.build_schedule_quality_target")
    @patch("backups.services.portfolio_reporting.fetch_rows")
    def test_resource_report_excludes_soft_deleted_assignments(
        self,
        fetch_rows,
        build_target,
    ):
        build_target.return_value.sql_database = "P6"
        fetch_rows.return_value = [
            {
                "resource_name": "Planner",
                "assignment_count": 4,
                "activity_count": 3,
                "project_count": 1,
                "planned_units": Decimal("20"),
                "actual_units": Decimal("8"),
                "remaining_units": Decimal("12"),
            }
        ]

        result = fetch_resource_overview(ScheduleQualityValidationFilters())

        sql = fetch_rows.call_args.args[1]
        self.assertIn("assignment.delete_session_id IS NULL", sql)
        self.assertIn("resource.delete_session_id IS NULL", sql)
        self.assertEqual(result["kpis"][1]["value"], 4)

    @patch("backups.services.portfolio_reporting._project_metric_rows")
    def test_portfolio_overview_builds_health_bands_and_intervention_order(self, metric_rows):
        metric_rows.return_value = [
            {
                "proj_id": 1,
                "proj_short_name": "Healthy",
                "project_status": "Client Submitted",
                "health_score": Decimal("92.00"),
                "health_band": "Good",
                "negative_float_count": 1,
                "critical_task_count": 2,
                "near_critical_task_count": 3,
            },
            {
                "proj_id": 2,
                "proj_short_name": "Intervention",
                "project_status": "Client Submitted",
                "health_score": Decimal("72.00"),
                "health_band": "Poor",
                "negative_float_count": 10,
                "critical_task_count": 20,
                "near_critical_task_count": 30,
            },
        ]

        result = fetch_portfolio_overview(object())

        self.assertEqual(result["top_projects"][0]["proj_short_name"], "Intervention")
        self.assertEqual(result["kpis"][0]["value"], 2)
        self.assertEqual(result["kpis"][1]["value"], 1)
        self.assertEqual(result["kpis"][3]["value"], 1)
        self.assertEqual(result["kpis"][4]["value"], "82.00%")
        self.assertEqual(result["status_rows"][0]["percent"], 100.0)


class PortfolioReportingViewTests(TestCase):
    def setUp(self):
        self.member = get_user_model().objects.create_user(
            username="portfolio-member",
            password="password",
        )
        self.non_member = get_user_model().objects.create_user(
            username="portfolio-non-member",
            password="password",
        )
        group, _ = Group.objects.get_or_create(
            name=settings.P6_PORTFOLIO_REPORTING_GROUP
        )
        self.member.groups.add(group)

    def test_hub_requires_portfolio_reporting_access(self):
        self.client.force_login(self.non_member)

        response = self.client.get(reverse("portfolio_reporting_hub"))

        self.assertEqual(response.status_code, 403)

    def test_hub_lists_all_six_report_destinations(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("portfolio_reporting_hub"))

        self.assertEqual(response.status_code, 200)
        for title in (
            "Portfolio Overview",
            "Milestone Governance",
            "Schedule Risk &amp; Float",
            "Schedule Health",
            "Project Detail",
            "Resources",
        ):
            self.assertContains(response, title)
        self.assertContains(response, 'href="/portfolio-reporting/overview/"')
        self.assertContains(response, 'href="/portfolio-reporting/resources/"')

    @patch(
        "backups.views.fetch_validation_filter_options",
        return_value={
            "projects": [
                {
                    "proj_id": 7,
                    "proj_short_name": "Project Seven",
                    "portfolio": "Rail",
                    "updated_date": date(2026, 8, 20),
                }
            ],
            "portfolios": ["Rail"],
            "lead_planners": ["Alex"],
            "project_statuses": ["Client Submitted"],
            "project_states": ["Live"],
            "updated_dates": [date(2026, 8, 20)],
            "checks": [],
        },
    )
    @patch("backups.views.PortfolioOverviewView.report_fetcher")
    def test_overview_renders_live_summary_and_shared_navigation(self, fetcher, _options):
        fetcher.return_value = {
            "kpis": [
                {"label": "Projects", "value": 12, "detail": "Current scope"},
                {"label": "Projects poor", "value": 2, "detail": "Below 80%", "tone": "poor"},
            ],
            "top_projects": [
                {
                    "proj_id": 7,
                    "proj_short_name": "Project Seven",
                    "portfolio": "Rail",
                    "lead_planner": "Alex",
                    "health_score": Decimal("72.50"),
                    "health_band": "Poor",
                    "negative_float_count": 8,
                    "critical_task_count": 4,
                    "near_critical_task_count": 6,
                }
            ],
            "status_rows": [{"label": "Client Submitted", "count": 12, "percent": 100}],
        }
        self.client.force_login(self.member)

        response = self.client.get(reverse("portfolio_reporting_overview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Portfolio Overview")
        self.assertContains(response, "Projects requiring intervention")
        self.assertContains(response, "Project Seven")
        self.assertContains(response, "72.50%")
        self.assertContains(response, "Milestone Governance")
        self.assertContains(response, 'hx-get="/portfolio-reporting/overview/"')
        self.assertContains(response, 'hx-trigger="change from:select, change from:input"')
        self.assertContains(response, 'hx-target=".portfolio-layout"')
        self.assertContains(response, 'hx-select=".portfolio-layout"')
        self.assertContains(response, 'hx-push-url="true"')
        self.assertContains(response, "syncPortfolioReportNavigation")
        self.assertContains(response, "Exclude blanks")
        self.assertNotContains(response, ">Apply filters<")
        self.assertContains(response, '<nav class="panel portfolio-report-nav"')
        self.assertContains(response, '<section class="panel portfolio-filters">')
        content = response.content.decode()
        self.assertLess(
            content.index('<div class="portfolio-layout">'),
            content.index('<nav class="panel portfolio-report-nav"'),
        )
        self.assertContains(response, "resetPortfolioReportNavigation")
        self.assertEqual(response.content.decode().count('class="portfolio-select"'), 8)
        fetcher.assert_called_once()

    @patch(
        "backups.views.fetch_validation_filter_options",
        return_value={
            "projects": [],
            "portfolios": ["Rail"],
            "lead_planners": [],
            "project_statuses": ["Client Submitted"],
            "project_states": ["Live"],
            "disciplines": [],
            "project_phases": [],
            "updated_dates": [],
            "checks": [],
        },
    )
    @patch("backups.views.PortfolioOverviewView.report_fetcher")
    def test_filters_persist_when_moving_between_portfolio_reports(self, _fetcher, _options):
        self.client.force_login(self.member)

        response = self.client.get(
            reverse("portfolio_reporting_overview"),
            {"portfolio": "Rail", "exclude_blanks": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'href="/portfolio-reporting/milestones/?portfolio=Rail&amp;exclude_blanks=1"',
        )

    @patch(
        "backups.views.fetch_validation_filter_options",
        return_value={
            "projects": [],
            "portfolios": [],
            "lead_planners": [],
            "project_statuses": [],
            "project_states": [],
            "updated_dates": [],
            "checks": [],
        },
    )
    def test_each_report_route_renders_its_page(self, _options):
        routes = (
            ("portfolio_reporting_milestones", "Milestone Governance"),
            ("portfolio_reporting_risk_float", "Schedule Risk &amp; Float"),
            ("portfolio_reporting_health", "Schedule Health"),
            ("portfolio_reporting_project_detail", "Project Detail"),
            ("portfolio_reporting_resources", "Resources"),
        )
        classes = (
            "MilestoneGovernanceView",
            "ScheduleRiskFloatView",
            "ScheduleHealthView",
            "PortfolioProjectDetailView",
            "ResourceReportingView",
        )
        self.client.force_login(self.member)

        for (route, title), class_name in zip(routes, classes):
            with self.subTest(route=route), patch(
                f"backups.views.{class_name}.report_fetcher",
                return_value={"kpis": [], "rows": [], "distribution": [], "project": None},
            ):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, title)

    def test_landing_card_and_nav_follow_quality_reports(self):
        quality_group, _ = Group.objects.get_or_create(
            name=settings.P6_SCHEDULE_QUALITY_REPORT_GROUP
        )
        self.member.groups.add(quality_group)
        self.client.force_login(self.member)

        response = self.client.get(reverse("suite_landing"))

        self.assertContains(response, "Open Portfolio Reporting")
        content = response.content.decode()
        self.assertLess(content.index("Open Quality Reports"), content.index("Open Portfolio Reporting"))
