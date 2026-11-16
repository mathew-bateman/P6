from django.urls import path
from reporting_mirror import views

urlpatterns = [
    path("reporting-database/", views.ReportingDashboardView.as_view(), name="reporting_mirror_dashboard"),
    path("reporting-database/settings/", views.ReportingSettingsView.as_view(), name="reporting_mirror_settings"),
    path("reporting-database/refresh/", views.ReportingRefreshView.as_view(), name="reporting_mirror_refresh"),
    path("reporting-database/validate-engine/", views.NativeEngineValidationView.as_view(), name="reporting_mirror_validate_engine"),
    path("reporting-database/views/<slug:name>/", views.ReportingPreviewView.as_view(), name="reporting_mirror_preview"),
]
