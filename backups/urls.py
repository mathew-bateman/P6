from __future__ import annotations

from django.urls import path

from backups import views


urlpatterns = [
    path("", views.LandingPageView.as_view(), name="suite_landing"),
    path("backup-targets/", views.DashboardView.as_view(), name="backup_dashboard"),
    path("maintenance/", views.MaintenanceDashboardView.as_view(), name="maintenance_dashboard"),
    path("schedule-quality/", views.ScheduleQualityDashboardView.as_view(), name="schedule_quality_dashboard"),
    path(
        "schedule-quality/overview/",
        views.ScheduleQualityOverviewView.as_view(),
        name="schedule_quality_overview",
    ),
    path(
        "schedule-quality/validation/",
        views.ScheduleQualityValidationView.as_view(),
        name="schedule_quality_validation",
    ),
    path(
        "portfolio-reporting/",
        views.PortfolioReportingHubView.as_view(),
        name="portfolio_reporting_hub",
    ),
    path(
        "portfolio-reporting/overview/",
        views.PortfolioOverviewView.as_view(),
        name="portfolio_reporting_overview",
    ),
    path(
        "portfolio-reporting/milestones/",
        views.MilestoneGovernanceView.as_view(),
        name="portfolio_reporting_milestones",
    ),
    path(
        "portfolio-reporting/risk-and-float/",
        views.ScheduleRiskFloatView.as_view(),
        name="portfolio_reporting_risk_float",
    ),
    path(
        "portfolio-reporting/schedule-health/",
        views.ScheduleHealthView.as_view(),
        name="portfolio_reporting_health",
    ),
    path(
        "portfolio-reporting/project-detail/",
        views.PortfolioProjectDetailView.as_view(),
        name="portfolio_reporting_project_detail",
    ),
    path(
        "portfolio-reporting/resources/",
        views.ResourceReportingView.as_view(),
        name="portfolio_reporting_resources",
    ),
    path("targets/<slug:slug>/", views.TargetDetailView.as_view(), name="backup_target_detail"),
    path("targets/<slug:slug>/update/", views.TargetUpdateView.as_view(), name="backup_target_update"),
    path("targets/<slug:slug>/backup/", views.TriggerBackupView.as_view(), name="backup_target_run"),
    path("targets/<slug:slug>/remote/", views.RemoteBackupListView.as_view(), name="backup_target_remote"),
    path("targets/<slug:slug>/restore/", views.TriggerRestoreView.as_view(), name="backup_target_restore"),
    path(
        "schedule-quality/refresh/",
        views.TriggerScheduleQualityRefreshView.as_view(),
        name="schedule_quality_refresh",
    ),
    path(
        "schedule-quality/schedule/",
        views.ScheduleQualityScheduleUpdateView.as_view(),
        name="schedule_quality_schedule_update",
    ),
    path(
        "schedule-quality/settings/",
        views.ScheduleQualitySettingsView.as_view(),
        name="schedule_quality_settings",
    ),
    path(
        "schedule-quality/settings/api/p6-fields/",
        views.ScheduleQualityP6FieldsApiView.as_view(),
        name="schedule_quality_p6_fields_api",
    ),
]
