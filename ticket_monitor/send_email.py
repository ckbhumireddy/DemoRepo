"""Email the ticket-availability summary using the repo's SMTP_*/EMAIL_* settings.

Reads SUMMARY from the environment. Exits non-zero if the email cannot be sent
so the workflow run is marked failed (GitHub's own failure email is then the
fallback alert).
"""

from __future__ import annotations

import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from earnings_notifier.config import Config  # noqa: E402
from earnings_notifier.notifier import EmailNotifier  # noqa: E402


def main() -> int:
    summary = os.environ.get("SUMMARY", "").strip() or "Tickets available."
    date = os.environ.get("TARGET_DATE", "")
    subject = os.environ.get("SUBJECT") or f"[TICKETS] Carlsbad Caverns tickets available for {date}"
    link = os.environ.get("LINK") or "https://www.recreation.gov/ticket/234637/ticket/10086811"
    link_text = os.environ.get("LINK_TEXT") or "Book on recreation.gov"
    body = f"{summary}\n\n{link}\n"
    html_body = (
        "<div style='font-family:Arial,Helvetica,sans-serif'>"
        f"<p><b>{html.escape(summary)}</b></p>"
        f"<p><a href='{html.escape(link)}'>{html.escape(link_text)}</a></p></div>"
    )
    config = Config.from_env()
    config.validate()
    EmailNotifier(config).send(subject, body, html_body)
    print("Alert email sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
