from __future__ import annotations

import requests


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
    response = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
