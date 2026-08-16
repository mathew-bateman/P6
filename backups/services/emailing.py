from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from graph.services.email import send_graph_email
from graph.services.tokens import get_app_token


def format_bytes(size_bytes: int) -> str:
    size = float(size_bytes or 0)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "bytes":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def send_backup_status_email(*, backup_run, success: bool) -> None:
    recipients = backup_run.target.notification_email_list
    if not recipients:
        return

    subject = (
        f"[{'SUCCESS' if success else 'FAILED'}] "
        f"P6 {backup_run.get_operation_display()}: {backup_run.target.name}"
    )
    context = {
        "backup_run": backup_run,
        "target": backup_run.target,
        "success": success,
        "size": format_bytes(backup_run.encrypted_size_bytes or backup_run.plaintext_size_bytes),
        "subject": subject,
        "company_name": getattr(settings, "P6_EMAIL_COMPANY_NAME", "Axial Projects"),
        "logo_url": getattr(settings, "P6_EMAIL_LOGO_URL", ""),
        "email_footer_content": "Automated P6 backup notification from Axial Projects.",
    }
    plain_body = render_to_string("backups/email_status.txt", context)
    html_body = render_to_string("emails/backup_status.html", context)

    sender = str(getattr(settings, "GRAPH_MAIL_SENDER", "")).strip()
    if sender:
        token = get_app_token()
        send_graph_email(
            token=token,
            sender=sender,
            to_emails=recipients,
            subject=subject,
            html_body=html_body,
        )
        return

    send_mail(
        subject=subject,
        message=plain_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipients,
        html_message=html_body,
        fail_silently=False,
    )
