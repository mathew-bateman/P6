"""Attach the bundled Axial logo for HTML that references its content ID."""
from base64 import b64encode
from email.mime.image import MIMEImage
from email.message import MIMEPart
from pathlib import Path

LOGO_CONTENT_ID = "axial-logo"
LOGO_FILENAME = "axial-logo-blue.png"


def logo_bytes():
    return Path(__file__).with_name(LOGO_FILENAME).read_bytes()


def uses_logo(html):
    return f"cid:{LOGO_CONTENT_ID}" in (html or "")


def graph_logo_attachments(html):
    if not uses_logo(html):
        return []
    return [{
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": LOGO_FILENAME,
        "contentType": "image/png",
        "contentBytes": b64encode(logo_bytes()).decode("ascii"),
        "contentId": LOGO_CONTENT_ID,
        "isInline": True,
    }]


def attach_inline_logo(message, html):
    if uses_logo(html):
        from django import VERSION
        if VERSION >= (6, 0):
            # Django 6 uses the modern email API and rejects mixed_subtype.
            image = MIMEPart()
            image.set_content(
                logo_bytes(), maintype="image", subtype="png",
                disposition="inline", cid=f"<{LOGO_CONTENT_ID}>",
                filename=LOGO_FILENAME,
            )
            message.attach(image)
            return message
        # Use a related container for an inline-only message. Preserve the
        # normal mixed container when the caller also supplied file attachments.
        if not message.attachments:
            message.mixed_subtype = "related"
        image = MIMEImage(logo_bytes(), _subtype="png")
        image.add_header("Content-ID", f"<{LOGO_CONTENT_ID}>")
        image.add_header("Content-Disposition", "inline", filename=LOGO_FILENAME)
        message.attach(image)
    return message
