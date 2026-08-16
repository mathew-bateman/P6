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
