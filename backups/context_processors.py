from backups.views import (
    can_edit_schedule_quality_settings,
    can_view_portfolio_reporting,
    can_view_schedule_quality_reports,
)
from backups.navigation import build_workspace_navigation, report_query, selected_report_source


def schedule_quality_access(request):
    """Expose report visibility consistently to the shared navigation and hub."""

    quality = can_view_schedule_quality_reports(request.user)
    portfolio = can_view_portfolio_reporting(request.user)
    editable = can_edit_schedule_quality_settings(request.user)
    navigation = build_workspace_navigation(request, can_view_quality=quality, can_view_portfolio=portfolio, can_edit_settings=editable) if request.user.is_authenticated else None
    return {
        "can_view_schedule_quality_reports": quality,
        "can_view_portfolio_reporting": portfolio,
        "workspace_can_edit_settings": editable,
        "workspace_navigation": navigation,
        "workspace_is_home": bool(navigation and navigation["is_home"]),
        "workspace_report_source": selected_report_source(request),
        "workspace_report_query": report_query(request),
        "workspace_reporting_mirror_installed": bool(navigation and navigation["mirror_installed"]),
        "workspace_reporting_mirror_available": bool(navigation and navigation["mirror_available"]),
    }
