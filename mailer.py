"""
mailer.py - Send Teams chat export as an email via SMTP.

Supports:
  - Gmail (smtp.gmail.com:587 with App Password)
  - Outlook / Hotmail (smtp-mail.outlook.com:587)
  - Any generic SMTP relay

The email body is an HTML table of messages for easy reading.
If the message list is large it is also attached as a JSON file.
"""

import json
import logging
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Maximum number of messages to render inline before switching to attachment-only
INLINE_LIMIT = 200


# ---------------------------------------------------------------------------
# HTML formatting helpers
# ---------------------------------------------------------------------------

def _format_sender(msg: Dict[str, Any]) -> str:
    sender = msg.get("from") or msg.get("imdisplayname") or ""
    if isinstance(sender, dict):
        sender = sender.get("user", {}).get("displayName", str(sender))
    return escape(str(sender))


def _format_time(msg: Dict[str, Any]) -> str:
    ts = msg.get("originalarrivaltime") or msg.get("composetime", "")
    return escape(str(ts)[:19].replace("T", " "))


def _format_content(msg: Dict[str, Any]) -> str:
    body = msg.get("content") or msg.get("body", {})
    if isinstance(body, dict):
        body = body.get("content", "")
    # Strip HTML tags for plain display
    body = re.sub(r"<[^>]+>", "", str(body))
    return escape(body[:2000])


def _build_html(messages: List[Dict[str, Any]], total_count: int) -> str:
    import re

    rows_html = ""
    for msg in messages[:INLINE_LIMIT]:
        sender = _format_sender(msg)
        time_str = _format_time(msg)
        content = _format_content(msg)
        msg_type = escape(str(msg.get("messageType", "")))
        rows_html += (
            f"<tr>"
            f"<td style='padding:4px 8px;color:#555'>{time_str}</td>"
            f"<td style='padding:4px 8px;font-weight:bold'>{sender}</td>"
            f"<td style='padding:4px 8px;color:#333'>{msg_type}</td>"
            f"<td style='padding:4px 8px'>{content}</td>"
            f"</tr>\n"
        )

    truncation_note = ""
    if total_count > INLINE_LIMIT:
        truncation_note = (
            f"<p style='color:#888'>Showing {INLINE_LIMIT} of {total_count} messages. "
            "Full export attached as JSON.</p>"
        )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;font-size:13px">
  <h2 style="color:#464775">Microsoft Teams Chat Export</h2>
  <p>Total new messages: <strong>{total_count}</strong></p>
  {truncation_note}
  <table border="1" cellspacing="0" cellpadding="0"
         style="border-collapse:collapse;width:100%;font-size:12px">
    <thead>
      <tr style="background:#464775;color:#fff">
        <th style="padding:6px 8px">Time</th>
        <th style="padding:6px 8px">From</th>
        <th style="padding:6px 8px">Type</th>
        <th style="padding:6px 8px">Message</th>
      </tr>
    </thead>
    <tbody>
{rows_html}
    </tbody>
  </table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_export(
    messages: List[Dict[str, Any]],
    email_cfg: Dict[str, Any],
    subject_suffix: str = "",
) -> bool:
    """
    Send messages via SMTP.  Returns True on success, False on failure.

    email_cfg keys:
      smtp_host, smtp_port, use_tls, sender, password, recipient,
      subject_prefix  (all from config.json)
    """
    if not messages:
        logger.info("No messages to send.")
        return True

    subject = (
        f"{email_cfg.get('subject_prefix', '[Teams Export]')} "
        f"{len(messages)} new message(s){subject_suffix}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_cfg["sender"]
    msg["To"] = email_cfg["recipient"]

    html_body = _build_html(messages, len(messages))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Attach full JSON export
    json_bytes = json.dumps(messages, ensure_ascii=False, indent=2).encode("utf-8")
    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(json_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header(
        "Content-Disposition",
        "attachment",
        filename="teams_chat_export.json",
    )
    msg.attach(attachment)

    try:
        smtp = smtplib.SMTP(
            email_cfg["smtp_host"],
            int(email_cfg["smtp_port"]),
            timeout=30,
        )
        if email_cfg.get("use_tls", True):
            smtp.starttls()
        smtp.login(email_cfg["sender"], email_cfg["password"])
        smtp.sendmail(email_cfg["sender"], [email_cfg["recipient"]], msg.as_bytes())
        smtp.quit()
        logger.info("Email sent to %s (%d messages).", email_cfg["recipient"], len(messages))
        return True
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False
