from django import forms
from reporting_mirror.models import ReportingConfig, ReportingRun
from reporting_mirror.connections import reporting_available
from reporting_mirror.sql_retirement import retired, MESSAGE


class ReportingSettingsForm(forms.ModelForm):
    class Meta:
        model = ReportingConfig
        fields = ["enabled", "serve_reports", "calculation_engine", "sql_parallel_enabled", "interval_minutes", "stale_after_minutes", "query_timeout_seconds", "retained_generations"]
        labels = {"enabled": "Run scheduled refreshes", "serve_reports": "Use PostgreSQL for quality and portfolio reports", "interval_minutes": "Refresh interval (minutes)", "stale_after_minutes": "Warn when source data is older than (minutes)", "query_timeout_seconds": "Report query timeout (seconds)", "retained_generations": "Previous successful snapshots to retain"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.previous_engine = self.instance.calculation_engine
        self.fields["calculation_engine"].required = False
        self.fields["calculation_engine"].label = "Reporting calculation engine"
        self.fields["sql_parallel_enabled"].label = "Keep existing P6 22 SQL reports refreshed in parallel"
        self.fields["sql_parallel_enabled"].help_text = "Refreshes the existing SQL reporting views and results after successful PostgreSQL updates and on their original schedule. Preserves SQL history and drafts. P6 23 has no legacy SQL reporting installation."
        if self.instance.pending_engine:
            self.fields["calculation_engine"].disabled = True
        if self.instance.manage_settings_in_postgres:
            self.fields['serve_reports'].disabled = True
            self.fields['serve_reports'].help_text = 'Required while PostgreSQL manages reporting rules. Use the calculation-engine selector to switch back to SQL calculations.'

        if retired():
            self.fields["calculation_engine"].choices = [("postgres", "Native PostgreSQL")]
            self.fields["calculation_engine"].disabled = True
            self.fields["sql_parallel_enabled"].disabled = True
            self.initial.update(calculation_engine="postgres", sql_parallel_enabled=False)
            self.fields["calculation_engine"].help_text = MESSAGE
            self.fields["sql_parallel_enabled"].help_text = "The retired SQL reports are no longer refreshed."
            self.fields["serve_reports"].help_text = "PostgreSQL manages reporting rules and serves these reports."

    def clean_calculation_engine(self):
        engine = self.cleaned_data.get("calculation_engine") or self.previous_engine
        if retired() and engine != "postgres":
            raise forms.ValidationError(MESSAGE)
        if engine != self.previous_engine and engine == "postgres":
            from reporting_mirror.native_pipeline import verified_native_engine
            if not verified_native_engine():
                raise forms.ValidationError("Complete a successful validation of the current PostgreSQL engine before switching.")
        return engine

    def clean(self):
        data = super().clean()
        if retired() and data.get("sql_parallel_enabled"):
            self.add_error("sql_parallel_enabled", MESSAGE)
        interval = data.get("interval_minutes", 60)
        if data.get("stale_after_minutes", 120) < interval:
            self.add_error("stale_after_minutes", "Allow at least one refresh interval before marking data as stale.")
        if data.get("serve_reports") and not ReportingRun.objects.filter(status="success").exists():
            self.add_error("serve_reports", "Complete a validated refresh before serving these reports.")
        if (data.get("serve_reports") or data.get("enabled")) and not reporting_available():
            self.add_error(None, "This host has no reporting database configured. Recovery hosts continue using their SQL reporting connection.")
        if data.get("sql_parallel_enabled") and (not self.instance.manage_settings_in_postgres or not reporting_available()):
            self.add_error("sql_parallel_enabled", "Parallel SQL reporting requires the active PostgreSQL settings store on this host.")
        return data
