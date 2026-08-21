from backups.views import (
    can_view_portfolio_reporting,
    can_view_schedule_quality_reports,
)


def schedule_quality_access(request):
    """Expose report visibility consistently to the shared navigation and hub."""

    return {
        "can_view_schedule_quality_reports": can_view_schedule_quality_reports(
            request.user
        ),
        "can_view_portfolio_reporting": can_view_portfolio_reporting(request.user),
    }
