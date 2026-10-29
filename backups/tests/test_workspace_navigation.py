from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import NoReverseMatch, resolve, reverse

from backups.context_processors import schedule_quality_access
from backups.navigation import report_query, section_for_route, selected_report_source


class WorkspaceRouteTests(SimpleTestCase):
    def test_each_route_family_has_the_correct_section(self):
        families = {
            "home": ["suite_landing"],
            "portfolio": ["portfolio_reporting_hub", "portfolio_reporting_overview", "portfolio_reporting_milestones", "portfolio_reporting_risk_float", "portfolio_reporting_health", "portfolio_reporting_project_detail", "portfolio_reporting_resources"],
            "quality": ["schedule_quality_overview", "schedule_quality_validation"],
            "settings": ["schedule_quality_settings", "schedule_quality_p6_fields_api"],
            "operations": ["backup_dashboard", "backup_target_detail", "backup_target_update", "backup_target_run", "backup_target_remote", "backup_target_restore", "maintenance_dashboard", "schedule_quality_dashboard", "schedule_quality_refresh", "schedule_quality_schedule_update"],
            "reporting": ["reporting_mirror_dashboard", "reporting_mirror_status"],
        }
        for expected, names in families.items():
            for name in names:
                with self.subTest(route=name):
                    self.assertEqual(section_for_route(name), expected)
        self.assertEqual(section_for_route(None), "")
        self.assertEqual(section_for_route("unknown"), "")

    def test_source_selection_accepts_only_known_sources_and_does_not_write_session(self):
        request = RequestFactory().get("/", {"report_source": "invalid"})
        request.p6_report_source = "p6_22"
        request.session = {"p6_report_source": "p6_23"}
        original = dict(request.session)
        self.assertEqual(selected_report_source(request), "p6_22")
        self.assertEqual(request.session, original)
        request.GET = RequestFactory().get("/", {"report_source": "combined"}).GET
        self.assertEqual(selected_report_source(request), "combined")
        del request.p6_report_source
        request.GET = RequestFactory().get("/").GET
        self.assertEqual(selected_report_source(request), "p6_23")

    def test_reset_queries_keep_only_valid_report_source(self):
        request = RequestFactory().get("/", {"report_source": "p6_23", "project": "7", "page": "3", "unexpected": "omit"})
        self.assertEqual(report_query(request), "?report_source=p6_23")
        self.assertEqual(parse_qs(report_query(request, preserve_filters=True)[1:]), {"report_source": ["p6_23"], "project": ["7"]})


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class WorkspaceNavigationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.viewer = User.objects.create_user(username="viewer")
        cls.staff = User.objects.create_user(username="staff", is_staff=True)
        cls.superuser = User.objects.create_user(username="superuser", is_staff=True, is_superuser=True)
        cls.quality = User.objects.create_user(username="quality.reader")
        cls.portfolio = User.objects.create_user(username="portfolio.reader")
        cls.editor = User.objects.create_user(username="quality.editor")
        for user, name in (
            (cls.quality, settings.P6_SCHEDULE_QUALITY_REPORT_GROUP),
            (cls.portfolio, settings.P6_PORTFOLIO_REPORTING_GROUP),
            (cls.editor, settings.P6_SCHEDULE_QUALITY_EDITOR_GROUP),
        ):
            group, _ = Group.objects.get_or_create(name=name)
            user.groups.add(group)

    def request(self, user, route="suite_landing", data=None):
        request = RequestFactory().get(reverse(route), data or {})
        request.user = user
        request.session = {}
        request.resolver_match = resolve(request.path)
        return request

    def context(self, user, route="suite_landing", data=None):
        request = self.request(user, route, data)
        return request, schedule_quality_access(request)

    def render_shell(self, request, context):
        return render_to_string("base.html", {"request": request, **context}, request=request)

    def test_anonymous_requests_have_no_workspace_navigation(self):
        request, context = self.context(AnonymousUser())
        self.assertIsNone(context["workspace_navigation"])
        html = self.render_shell(request, context)
        self.assertNotIn('data-workspace-navigation', html)
        self.assertNotIn('id="workspace-sidebar"', html)

    def test_every_authenticated_user_has_home_and_data_refresh_only_by_default(self):
        _, context = self.context(self.viewer)
        sections = context["workspace_navigation"]["sections"]
        self.assertEqual([item["id"] for item in sections], ["home", "operations"])
        self.assertEqual(sections[1]["url"], reverse("schedule_quality_dashboard"))
        links = [link["id"] for group in sections[1]["groups"] for link in group["links"]]
        self.assertEqual(links, ["schedule_quality_dashboard"])

    def test_staff_and_superuser_do_not_gain_report_groups(self):
        for user in (self.staff, self.superuser):
            with self.subTest(user=user.username):
                _, context = self.context(user)
                ids = [section["id"] for section in context["workspace_navigation"]["sections"]]
                self.assertNotIn("quality", ids)
                self.assertNotIn("portfolio", ids)
                self.assertIn("settings", ids)
                operations = next(section for section in context["workspace_navigation"]["sections"] if section["id"] == "operations")
                self.assertEqual([group["id"] for group in operations["groups"]], ["backups", "maintenance", "refresh"])

    def test_report_groups_are_independent_and_editor_does_not_gain_reports(self):
        for user, included, excluded in (
            (self.quality, "quality", {"portfolio", "settings"}),
            (self.portfolio, "portfolio", {"quality", "settings"}),
            (self.editor, "settings", {"quality", "portfolio"}),
        ):
            with self.subTest(user=user.username):
                _, context = self.context(user)
                ids = {section["id"] for section in context["workspace_navigation"]["sections"]}
                self.assertIn(included, ids)
                self.assertFalse(ids & excluded)

    def test_home_has_rail_and_no_header_secondary_sidebar_or_breadcrumbs(self):
        request, context = self.context(self.quality)
        html = self.render_shell(request, context)
        self.assertIn('data-workspace-navigation', html)
        self.assertIn('workspace-home', html)
        self.assertNotIn('<header', html)
        self.assertNotIn('id="workspace-sidebar"', html)
        self.assertNotIn('workspace-mobile-toolbar', html)
        self.assertNotIn('aria-label="Breadcrumb"', html)
        self.assertNotIn('type="search"', html)
        self.assertNotIn('Search navigation', html)

    def test_sidebar_contains_only_active_section_choices(self):
        request, context = self.context(self.quality, "schedule_quality_validation")
        html = self.render_shell(request, context)
        sidebar = html.split('<aside id="workspace-sidebar"', 1)[1].split('</aside>', 1)[0]
        self.assertIn('Programme check', sidebar)
        self.assertIn('Validation &amp; evidence', sidebar)
        self.assertNotIn('Data refresh', sidebar)
        self.assertNotIn('Milestones', sidebar)
        self.assertIn('aria-current="page"', sidebar)

    def test_rail_resets_filters_while_same_section_sidebar_preserves_them(self):
        _, context = self.context(self.portfolio, "portfolio_reporting_overview", {"report_source": "p6_22", "portfolio": "Rail", "project": "7", "page": "4", "unexpected": "omit"})
        section = next(item for item in context["workspace_navigation"]["sections"] if item["id"] == "portfolio")
        self.assertEqual(parse_qs(urlsplit(section["url"]).query), {"report_source": ["p6_22"]})
        milestone = section["groups"][0]["links"][1]
        self.assertEqual(parse_qs(urlsplit(milestone["url"]).query), {"report_source": ["p6_22"], "portfolio": ["Rail"], "project": ["7"]})
        self.assertEqual(parse_qs(urlsplit(milestone["reset_url"]).query), {"report_source": ["p6_22"]})

    def test_cross_section_links_do_not_carry_report_filters(self):
        group = Group.objects.get(name=settings.P6_PORTFOLIO_REPORTING_GROUP)
        self.quality.groups.add(group)
        _, context = self.context(self.quality, "schedule_quality_overview", {"report_source": "p6_23", "check": "high_float", "project": "7"})
        portfolio = next(item for item in context["workspace_navigation"]["sections"] if item["id"] == "portfolio")
        self.assertEqual(parse_qs(urlsplit(portfolio["groups"][0]["links"][0]["url"]).query), {"report_source": ["p6_23"]})

    def test_logout_stays_a_csrf_protected_post_form(self):
        request, context = self.context(self.viewer)
        html = self.render_shell(request, context)
        self.assertIn(f'<form method="post" action="{reverse("logout")}">', html)
        self.assertIn('name="csrfmiddlewaretoken"', html)
        self.assertIn('aria-label="Account menu"', html)

    def test_optional_mirror_requires_installed_app_and_reversible_route(self):
        for installed, reversible in ((False, True), (True, False), (True, True)):
            with self.subTest(installed=installed, reversible=reversible):
                request = self.request(self.staff)
                original_reverse = reverse
                def optional_reverse(name):
                    if name == "reporting_mirror_dashboard":
                        if reversible:
                            return "/reporting-database/"
                        raise NoReverseMatch(name)
                    return original_reverse(name)
                with patch("backups.navigation.apps.is_installed", return_value=installed), patch("backups.navigation.reverse", side_effect=optional_reverse):
                    context = schedule_quality_access(request)
                ids = {section["id"] for section in context["workspace_navigation"]["sections"]}
                self.assertEqual("reporting" in ids, installed and reversible)

    def test_optional_mirror_does_not_grant_access_to_an_ordinary_viewer(self):
        request = self.request(self.viewer)
        original_reverse = reverse
        with patch("backups.navigation.apps.is_installed", return_value=True), patch("backups.navigation.reverse", side_effect=lambda name: "/reporting-database/" if name == "reporting_mirror_dashboard" else original_reverse(name)):
            context = schedule_quality_access(request)
        self.assertFalse(context["workspace_reporting_mirror_available"])
        self.assertNotIn("reporting", {section["id"] for section in context["workspace_navigation"]["sections"]})

    def test_backup_detail_keeps_operations_section_and_target_link_selected(self):
        request = self.request(self.staff, "backup_dashboard")
        request.resolver_match = SimpleNamespace(url_name="backup_target_detail")
        context = schedule_quality_access(request)
        section = context["workspace_navigation"]["active_section"]
        self.assertEqual(section["id"], "operations")
        self.assertTrue(section["groups"][0]["links"][0]["active"])
