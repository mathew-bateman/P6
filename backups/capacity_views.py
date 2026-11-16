from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import TemplateView

from django.utils.dateparse import parse_datetime

from backups.services.capacity_review import capacity_card_rows, collect_capacity_report


class DatabaseCapacityView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "backups/database_capacity.html"

    def test_func(self):
        return self.request.user.is_staff

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        report = collect_capacity_report()
        rows = capacity_card_rows(report)
        context["databases"] = rows
        context["capacity_report"] = report
        context["checked_at"] = parse_datetime(report["checked_at"])
        context["available_count"] = sum(row["status"] == "ok" for row in rows)
        return context
