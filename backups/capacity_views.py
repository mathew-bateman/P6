from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import TemplateView

from backups.models import BackupTarget
from backups.services.database_capacity import collect_database_capacity


class DatabaseCapacityView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "backups/database_capacity.html"

    def test_func(self):
        return self.request.user.is_staff

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rows = collect_database_capacity(BackupTarget.objects.order_by("name"), include_log_usage=True)
        context["databases"] = rows
        context["available_count"] = sum(row["status"] == "ok" for row in rows)
        return context
