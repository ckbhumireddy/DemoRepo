"""Email delivery over SMTP."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate

from .config import Config

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Sends a multipart (text + HTML) email via SMTP.

    Supports both STARTTLS (port 587, ``SMTP_USE_TLS``) and implicit TLS
    (port 465, ``SMTP_USE_SSL``). In ``dry_run`` mode nothing is sent — the
    message is logged instead.
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    def _build_message(self, subject: str, text_body: str, html_body: str) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.config.email_from
        msg["To"] = ", ".join(self.config.email_to)
        msg["Date"] = formatdate(localtime=True)
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
        return msg

    def send(self, subject: str, text_body: str, html_body: str) -> None:
        if self.config.dry_run:
            logger.info(
                "[dry-run] would send email to %s\nSubject: %s\n\n%s",
                ", ".join(self.config.email_to) or "(no recipients)",
                subject,
                text_body,
            )
            return

        msg = self._build_message(subject, text_body, html_body)
        cfg = self.config

        if cfg.smtp_use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context,
                                  timeout=cfg.request_timeout) as server:
                self._authenticate_and_send(server, msg)
        else:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port,
                              timeout=cfg.request_timeout) as server:
                server.ehlo()
                if cfg.smtp_use_tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                self._authenticate_and_send(server, msg)

        logger.info("Sent earnings digest to %s", ", ".join(cfg.email_to))

    def _authenticate_and_send(self, server: smtplib.SMTP, msg: EmailMessage) -> None:
        if self.config.smtp_user:
            server.login(self.config.smtp_user, self.config.smtp_password)
        server.send_message(msg)
