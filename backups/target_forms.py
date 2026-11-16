from django import forms
from django.utils.text import slugify

from backups.models import BackupTarget


class BackupTargetCreateForm(forms.ModelForm):
    class Meta:
        model = BackupTarget
        fields = ("name", "sql_host", "sql_port", "sql_database", "sql_username", "sql_password_env", "app_backup_dir", "sql_backup_dir", "sharepoint_site", "sharepoint_folder")
        labels = {"name": "Target name", "sql_host": "SQL host", "sql_port": "SQL port", "sql_database": "Database", "sql_username": "SQL user", "sql_password_env": "SQL password", "app_backup_dir": "App backup path", "sql_backup_dir": "SQL backup path", "sharepoint_site": "SharePoint site", "sharepoint_folder": "SharePoint folder"}
        widgets = {"sql_password_env": forms.PasswordInput(render_value=False)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.fields:
            self.fields[name].required = True

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        slug = slugify(name)[:80].strip("-")
        if not slug:
            raise forms.ValidationError("Enter a name containing letters or numbers.")
        if BackupTarget.objects.filter(slug=slug).exists():
            raise forms.ValidationError("A target with this name already exists.")
        return name

    def save(self, commit=True):
        target = super().save(commit=False)
        target.slug = slugify(self.cleaned_data["name"])[:80].strip("-")
        if commit:
            target.save()
        return target
