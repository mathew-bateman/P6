from __future__ import annotations

import requests
from graph.services.diagnostics import checked_request
from graph.services.inline_logo import graph_logo_attachments


def send_graph_email(
    *,
    token: str,
    sender: str,
    to_emails: list[str],
    subject: str,
    html_body: str,
    save_to_sent_items: bool = False,
) -> None:
    if not to_emails:
        return

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [
                {"emailAddress": {"address": email}}
                for email in to_emails
            ],
        },
        "saveToSentItems": save_to_sent_items,
    }
    attachments = graph_logo_attachments(html_body)
    if attachments:
        payload["message"]["attachments"] = attachments
    checked_request("Microsoft email delivery", requests.post,
        f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
